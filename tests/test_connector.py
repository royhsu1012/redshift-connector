import math
import os
import subprocess
import sys
import textwrap
import zipfile
from types import SimpleNamespace

import jpype
import pandas as pd
import pytest

from redshift_connector_bade import RedshiftClient, connector, fetch_data_from_redshift, query_redshift

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class JavaString:
    """JDBC 回傳的文字是 java.lang.String 物件，不是 Python 的 str。"""

    def __init__(self, value):
        self._value = value

    def __str__(self):
        return self._value


COLUMNS = [" model ", "qty", "price", "note", "tags"]
ROWS = [
    (JavaString(" A1 "), 1, 1.5, None, (JavaString("x "), JavaString(" y"))),
    (JavaString("B2"), 2, None, JavaString(" z "), None),
]


class FakeCursor:
    def __init__(self, rows, columns):
        self._rows = list(rows)
        self.description = [(c,) for c in columns] if columns else None
        self.closed = False

    def execute(self, query):
        self.query = query

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, size):
        rows, self._rows = self._rows[:size], self._rows[size:]
        return rows

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, db):
        self._db = db
        self.closed = False

    def cursor(self):
        return FakeCursor(self._db.rows, self._db.columns)

    def close(self):
        self.closed = True


@pytest.fixture
def db(monkeypatch):
    """以假連線取代 JVM 與 JDBC，DataFrame 的處理邏輯不需要資料庫就能測。"""
    state = SimpleNamespace(rows=ROWS, columns=COLUMNS, connects=[], jvm=[], conn=None)

    def fake_connect(driver, url, args, jars):
        state.connects.append(SimpleNamespace(driver=driver, url=url, jars=jars))
        state.conn = FakeConnection(state)
        return state.conn

    monkeypatch.setattr(connector, "_start_jvm", lambda jvm_path, jars: state.jvm.append((jvm_path, jars)))
    monkeypatch.setattr(connector.jaydebeapi, "connect", fake_connect)
    return state


# ---------- 查詢結果 ----------

def test_query_keeps_numbers_and_none(db):
    df = RedshiftClient("jdbc:redshift://x").query("select 1")

    assert list(df.columns) == ["model", "qty", "price", "note", "tags"]
    assert df["model"].tolist() == ["A1", "B2"]
    assert df["qty"].tolist() == [1, 2]
    assert pd.api.types.is_numeric_dtype(df["price"])
    assert df["price"][0] == 1.5 and math.isnan(df["price"][1])
    # NULL 維持缺值；實際是 None 還是 NaN 由 pandas 版本決定
    assert pd.isna(df["note"][0]) and df["note"][1] == "z"
    assert df["tags"][0] == "xy" and pd.isna(df["tags"][1])


def test_all_str_matches_0_1_behaviour(db):
    df = RedshiftClient("jdbc:redshift://x").query("select 1", all_str=True)

    assert df["qty"].tolist() == ["1", "2"]
    assert df["price"].tolist() == ["1.5", "nan"]
    assert df["note"].tolist() == ["None", "z"]
    assert df["tags"].tolist() == ["xy", "None"]


def test_chunked_result_equals_single_fetch(db):
    db.columns = ["id", "name"]
    db.rows = [(i, f" n{i} ") for i in range(10)]

    whole = RedshiftClient("jdbc:redshift://x").query("select 1")
    chunked = RedshiftClient("jdbc:redshift://x").query("select 1", chunk_size=3)

    pd.testing.assert_frame_equal(whole, chunked)


def test_empty_result_keeps_columns(db):
    db.rows = []
    for chunk_size in (None, 5):
        df = RedshiftClient("jdbc:redshift://x").query("select 1", chunk_size=chunk_size)
        assert df.empty and list(df.columns) == ["model", "qty", "price", "note", "tags"]


def test_statement_without_result_set_returns_empty_frame(db):
    db.columns = None
    assert RedshiftClient("jdbc:redshift://x").query("create table t (a int)").empty


# ---------- 連線 ----------

def test_connection_is_opened_once_and_reused(db):
    client = RedshiftClient("jdbc:redshift://x")
    assert db.connects == []          # 建立 client 時還不連線

    client.query("select 1")
    client.query("select 2")
    assert len(db.connects) == 1      # 多次查詢只登入一次

    first = db.conn
    client.close()
    assert first.closed
    client.query("select 3")
    assert len(db.connects) == 2      # close 之後才會重新連線


def test_defaults_use_bundled_driver_and_auto_jvm(db):
    RedshiftClient("jdbc:redshift://x").query("select 1")

    assert db.jvm == [(None, connector.bundled_jars())]
    assert db.connects[0].driver == "com.amazon.redshift.jdbc42.Driver"
    assert db.connects[0].jars == connector.bundled_jars()


def test_query_redshift_closes_connection(db):
    df = query_redshift("jdbc:redshift://x", "select 1")
    assert len(df) == 2 and db.conn.closed


# ---------- 0.1.x 舊介面 ----------

def test_legacy_call_with_explicit_paths(db):
    df = fetch_data_from_redshift("jvm.dll", "driver.jar", "sdk.jar", "jdbc:redshift://x", "select 1")

    assert db.jvm == [("jvm.dll", ["driver.jar", "sdk.jar"])]
    assert db.connects[0].jars == ["driver.jar", "sdk.jar"]
    assert df["qty"].tolist() == ["1", "2"]   # 舊介面一律回傳字串
    assert db.conn.closed


def test_legacy_call_with_none_paths_uses_bundled_driver(db):
    fetch_data_from_redshift(None, None, None, "jdbc:redshift://x", "select 1")
    assert db.jvm == [(None, connector.bundled_jars())]


# ---------- 內建驅動 ----------

def test_bundled_jars_are_exactly_the_official_driver_set():
    """驅動 jar 的 manifest 列出它需要的相依 jar；內建的檔案必須剛好是這一組。

    更新驅動版本時若漏放、多放（例如不需要的 aws-java-sdk-bundle）都會在這裡失敗。
    """
    names = {os.path.basename(j) for j in connector.bundled_jars()}
    drivers = [n for n in names if n.startswith("redshift-jdbc42-")]
    assert len(drivers) == 1

    with zipfile.ZipFile(os.path.join(connector._JAR_DIR, drivers[0])) as jar:
        manifest = jar.read("META-INF/MANIFEST.MF").decode("utf-8")
    manifest = manifest.replace("\r\n", "\n").replace("\n ", "")   # 還原 72 字元折行
    class_path = next(line for line in manifest.split("\n") if line.startswith("Class-Path:"))

    assert names == {drivers[0], *class_path[len("Class-Path:"):].split()}


def _java_available():
    try:
        connector.find_jvm()
        return True
    except RuntimeError:
        return False


needs_java = pytest.mark.skipif(not _java_available(), reason="需要安裝 Java")


def _jpype_finds_nothing():
    raise jpype.JVMNotFoundException("simulated")


@needs_java
def test_find_jvm_falls_back_when_jpype_detection_fails(monkeypatch):
    """模擬只把 java 放進 PATH、或剛裝完 Java 的機器：JPype 自己找不到，仍要能找到 JVM。"""
    monkeypatch.setattr(connector.jpype, "getDefaultJVMPath", _jpype_finds_nothing)

    assert os.path.isfile(connector.find_jvm())


def test_find_jvm_reports_missing_java(monkeypatch):
    monkeypatch.setattr(connector.jpype, "getDefaultJVMPath", _jpype_finds_nothing)
    monkeypatch.setattr(connector.shutil, "which", lambda name: None)
    monkeypatch.delenv("ProgramFiles", raising=False)

    with pytest.raises(RuntimeError, match="找不到 Java"):
        connector.find_jvm()


@needs_java
def test_self_check_command():
    result = subprocess.run(
        [sys.executable, "-m", "redshift_connector_bade"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "驅動載入成功" in result.stdout


@needs_java
def test_bundled_driver_works_in_a_real_jvm():
    """用真的 JVM 載入內建驅動，連到本機關閉的 port（不需要資料庫，也不會對外連線）。"""
    code = textwrap.dedent(
        """
        import jpype
        from redshift_connector_bade import RedshiftClient

        try:
            RedshiftClient("jdbc:redshift://127.0.0.1:1/dev?user=u&password=p").query("select 1")
        except Exception as e:
            assert "refused" in str(e), e      # 驅動已載入，並且實際嘗試了連線
        else:
            raise SystemExit("connected to a closed port?")

        # 瀏覽器登入外掛（Azure OAuth2 等）用到的類別，都必須能從內建 jar 載入
        for name in (
            "com.amazon.redshift.plugin.BrowserAzureOAuth2CredentialsProvider",
            "com.amazonaws.util.json.Jackson",
            "com.fasterxml.jackson.databind.JsonNode",
            "org.apache.http.impl.client.CloseableHttpClient",
            "org.apache.http.client.utils.URIBuilder",
        ):
            jpype.JClass(name)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr
