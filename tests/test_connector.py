import math
import os
import shutil
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
        self.valid = True
        self.jconn = SimpleNamespace(isValid=lambda timeout: self.valid)

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

def test_query_result_types(db):
    df = RedshiftClient("jdbc:redshift://x").query("select 1")

    assert list(df.columns) == ["model", "qty", "price", "note", "tags"]
    assert df["qty"].tolist() == [1, 2]
    assert pd.api.types.is_numeric_dtype(df["price"])
    assert df["price"][0] == 1.5 and math.isnan(df["price"][1])
    # 文字欄：去空白的字串，NULL 是 None，型別是 object
    assert df["model"].tolist() == ["A1", "B2"]
    assert df["note"].tolist() == [None, "z"]
    assert df["tags"].tolist() == ["xy", None]
    assert df["model"].dtype == object and df["note"].dtype == object


def test_text_columns_do_not_depend_on_pandas_version(db):
    """jaydebeapi 把日期轉成 Python 字串；讓 pandas 3 自行推斷的話，這種欄位的 None 會變成 NaN。"""
    db.columns = ["day"]
    db.rows = [("2026-01-01",), (None,)]

    df = RedshiftClient("jdbc:redshift://x").query("select 1")

    assert df["day"].dtype == object
    assert df["day"].tolist() == ["2026-01-01", None]


def test_all_str_matches_0_1_behaviour(db):
    df = RedshiftClient("jdbc:redshift://x").query("select 1", all_str=True)

    assert df["model"].tolist() == ["A1", "B2"]
    assert df["qty"].tolist() == ["1", "2"]
    assert df["price"].tolist() == ["1.5", "nan"]
    assert df["note"].tolist() == ["None", "z"]
    assert df["tags"].tolist() == ["xy", "None"]


def test_chunked_result_equals_single_fetch(db):
    db.columns = ["id", "name"]
    db.rows = [(i, JavaString(f" n{i} ")) for i in range(10)]

    whole = RedshiftClient("jdbc:redshift://x").query("select 1")
    chunked = RedshiftClient("jdbc:redshift://x").query("select 1", chunk_size=3)

    pd.testing.assert_frame_equal(whole, chunked)


def test_chunk_holding_only_nulls_does_not_change_the_types(db):
    """第一批的 amount 整欄都是 NULL：分批的結果仍要和一次讀完相同。"""
    db.columns = ["id", "amount", "name"]
    db.rows = [(1, None, None), (2, None, None), (3, 1.5, JavaString("a")), (4, 2.5, None)]

    whole = RedshiftClient("jdbc:redshift://x").query("select 1")
    chunked = RedshiftClient("jdbc:redshift://x").query("select 1", chunk_size=2)

    pd.testing.assert_frame_equal(whole, chunked)
    assert pd.api.types.is_float_dtype(chunked["amount"])
    assert chunked["name"].tolist() == [None, None, "a", None]


def test_duplicate_column_names_are_kept_apart(db):
    db.columns = ["id", "id"]
    db.rows = [(1, JavaString("a")), (2, JavaString("b"))]

    for chunk_size in (None, 1):
        df = RedshiftClient("jdbc:redshift://x").query("select a.id, b.id", chunk_size=chunk_size)
        assert list(df.columns) == ["id", "id"]
        assert df.iloc[:, 0].tolist() == [1, 2]
        assert df.iloc[:, 1].tolist() == ["a", "b"]


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


def test_dropped_connection_is_replaced(db):
    client = RedshiftClient("jdbc:redshift://x")
    client.query("select 1")

    db.conn.valid = False             # 伺服器中斷了連線（閒置過久、登入逾期）
    df = client.query("select 2")

    assert len(db.connects) == 2 and len(df) == 2


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


needs_java = pytest.mark.skipif(not _java_available(), reason="需要可用的 Java")


# ---------- 尋找 Java ----------

def _fake_java(tmp_path, name, version):
    """做一個只有 release 檔與空 jvm.dll 的假 Java 安裝目錄。"""
    home = tmp_path / name
    (home / "bin" / "server").mkdir(parents=True)
    if version:
        (home / "release").write_text(f'IMPLEMENTOR="Test"\nJAVA_VERSION="{version}"\n', encoding="utf-8")
    jvm = home / "bin" / "server" / "jvm.dll"
    jvm.write_bytes(b"")
    return str(jvm)


def _installed(monkeypatch, jvms, jpype_needs):
    monkeypatch.setattr(connector, "_jvm_candidates", lambda: iter(jvms))
    monkeypatch.setattr(connector, "_jpype_min_java", lambda: jpype_needs)


def test_java_version_is_read_from_the_release_file(tmp_path):
    assert connector._java_version(_fake_java(tmp_path, "jre8", "1.8.0_451")) == 8
    assert connector._java_version(_fake_java(tmp_path, "jre21", "21.0.4")) == 21
    assert connector._java_version(_fake_java(tmp_path, "unknown", None)) is None


def test_jpype_requirement_is_read_from_the_installed_jpype():
    assert connector._jpype_min_java() >= 8


def test_default_java_is_used_when_it_is_new_enough(monkeypatch, tmp_path):
    java8, java21 = _fake_java(tmp_path, "jre8", "1.8.0_451"), _fake_java(tmp_path, "jre21", "21.0.4")
    _installed(monkeypatch, [java8, java21], jpype_needs=8)

    assert connector.find_jvm() == java8


def test_java_too_old_for_jpype_is_skipped(monkeypatch, tmp_path):
    """JAVA_HOME 指向 Java 8，但新版 JPype 需要 Java 9 以上：改用電腦上較新的 Java。"""
    java8, java21 = _fake_java(tmp_path, "jre8", "1.8.0_451"), _fake_java(tmp_path, "jre21", "21.0.4")
    _installed(monkeypatch, [java8, java21], jpype_needs=9)

    assert connector.find_jvm() == java21


def test_only_a_too_old_java_gives_a_clear_message(monkeypatch, tmp_path):
    _installed(monkeypatch, [_fake_java(tmp_path, "jre8", "1.8.0_451")], jpype_needs=9)

    with pytest.raises(RuntimeError, match="第 8 版.*需要 Java 9 以上"):
        connector.find_jvm()


def test_missing_java_gives_a_clear_message(monkeypatch):
    _installed(monkeypatch, [], jpype_needs=9)

    with pytest.raises(RuntimeError, match="找不到 Java"):
        connector.find_jvm()


def test_java_is_found_without_jpype_detection(monkeypatch):
    """只把 java 放進 PATH、或剛裝完 Java 的機器：JPype 自己找不到，仍要列得出 JVM。"""
    def jpype_finds_nothing():
        raise jpype.JVMNotFoundException("simulated")

    if not (shutil.which("java") or os.environ.get("ProgramFiles")):
        pytest.skip("這台機器沒有其他找得到 Java 的途徑")
    monkeypatch.setattr(connector.jpype, "getDefaultJVMPath", jpype_finds_nothing)

    found = list(connector._jvm_candidates())
    assert found and all(os.path.isfile(path) for path in found)


def test_self_check_reports_unusable_java(monkeypatch, capsys):
    from redshift_connector_bade import __main__ as self_check

    _installed(monkeypatch, [], jpype_needs=9)

    assert self_check.main(["prog"]) == 2      # install.bat 看到 2 就會安裝 Java
    assert "找不到 Java" in capsys.readouterr().out


def test_self_check_command_never_crashes():
    """不論這台機器的 Java 能不能用，自我檢查都要給出結論，不能丟出 traceback。"""
    result = subprocess.run(
        [sys.executable, "-m", "redshift_connector_bade"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert "Traceback" not in result.stderr, result.stderr
    if _java_available():
        assert result.returncode == 0 and "驅動載入成功" in result.stdout, result.stdout
    else:
        assert result.returncode == 2 and "Java" in result.stdout, result.stdout


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


@needs_java
def test_driver_loads_from_a_path_with_spaces_and_non_ascii_characters(tmp_path):
    """套件裝在中文使用者名稱、或含空白的路徑底下時，驅動也要能載入。"""
    jar_dir = tmp_path / "使用者 王小明 Renée" / "jars"
    shutil.copytree(connector._JAR_DIR, jar_dir)
    code = textwrap.dedent(
        """
        import glob, os, sys
        from redshift_connector_bade import RedshiftClient

        jars = sorted(glob.glob(os.path.join(sys.argv[1], "*.jar")))
        try:
            RedshiftClient("jdbc:redshift://127.0.0.1:1/dev?user=u&password=p", jars=jars).query("select 1")
        except Exception as e:
            assert "refused" in str(e), e
        else:
            raise SystemExit("connected to a closed port?")
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(jar_dir)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr
