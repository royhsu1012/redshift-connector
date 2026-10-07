#!/usr/bin/env python
# coding: utf-8

import glob
import os
import re
import shutil
import subprocess

import jpype
import jaydebeapi
import pandas as pd

DRIVER_CLASS = "com.amazon.redshift.jdbc42.Driver"
_JAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jars")


def bundled_jars():
    """套件內建的 Redshift JDBC 驅動與相依 jar（AWS 官方 zip 的內容）。"""
    return sorted(glob.glob(os.path.join(_JAR_DIR, "*.jar")))


def find_jvm():
    """
    自動尋找 JVM，回傳 jvm.dll / libjvm 的路徑；找不到時拋出 RuntimeError。
    """
    # 1. JPype 內建的偵測：JAVA_HOME、Windows 登錄檔、各平台的慣用位置
    try:
        return jpype.getDefaultJVMPath()
    except jpype.JVMNotFoundException:
        pass

    # 2. 有些安裝方式只把 java 放進 PATH：直接問 java 自己裝在哪
    java = shutil.which("java")
    if java:
        result = subprocess.run([java, "-XshowSettings:properties", "-version"],
                                capture_output=True, text=True)
        match = re.search(r"java\.home = (.+)", result.stdout + result.stderr)
        if match:
            home = match.group(1).strip()
            for pattern in ("bin/server/jvm.dll", "lib/server/libjvm.*", "lib/*/server/libjvm.*"):
                found = sorted(glob.glob(os.path.join(home, *pattern.split("/"))))
                if found:
                    return found[-1]

    # 3. Windows 剛裝完 Java、PATH 還沒更新時：直接找 Program Files
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        for pattern in ("*/*/bin/server/jvm.dll", "*/*/jre/bin/server/jvm.dll"):
            found = sorted(glob.glob(os.path.join(program_files, *pattern.split("/"))))
            if found:
                return found[-1]

    raise RuntimeError("找不到 Java。請安裝 Java 8 以上版本，或設定 JAVA_HOME，或傳入 jvm_path。")


def _start_jvm(jvm_path, jars):
    # 一個行程只能啟動一次 JVM；若別處已啟動，就把 jar 補進 classpath
    if jpype.isJVMStarted():
        for jar in jars:
            jpype.addClassPath(jar)
        return

    jpype.startJVM(jvm_path or find_jvm(), classpath=jars)


def _clean_cell(x):
    """清理 JDBC 回傳的單格資料：數值與 None 原樣保留，其餘轉成去空白的字串。"""
    if isinstance(x, (tuple, list)):
        return ''.join([str(i).strip() for i in x])
    if x is None or isinstance(x, (int, float)):
        return x
    return str(x).strip()


def _to_series(values):
    """
    把一欄資料轉成 Series：全是數值的欄位用數值型別（NULL 為 NaN），
    其餘一律是 object 型別，內容為字串與 None。

    文字欄不交給 pandas 自行推斷：pandas 3 會把 None 變成 NaN、型別變成 str，
    同一段程式的結果會隨安裝的 pandas 版本而不同。
    """
    raw = pd.Series(list(values), dtype=object)
    numeric = raw.infer_objects()
    if pd.api.types.is_numeric_dtype(numeric):
        return numeric
    return pd.Series([_clean_cell(x) for x in raw], dtype=object)


def _from_series(series):
    # 欄位先以位置編號，最後才套上欄名：查詢結果有重複欄名時不會互相覆蓋
    return pd.DataFrame(dict(enumerate(series)))


def _to_dataframe(rows, n_columns):
    cells = list(zip(*rows)) if rows else [()] * n_columns
    return _from_series([_to_series(values) for values in cells])


def _concat_chunks(parts):
    """
    合併分批讀取的結果。

    各批次是分開判斷型別的：數值欄若在某一批剛好整欄都是 NULL，那一批會被當成文字欄。
    同一欄在各批次的型別不一致時，把值攤平重新判斷一次，結果才會和一次讀完相同。
    """
    series = []
    for i in parts[0].columns:
        chunks = [part[i] for part in parts]
        if len({chunk.dtype for chunk in chunks}) == 1:
            series.append(pd.concat(chunks, ignore_index=True))
        else:
            series.append(_to_series([x for chunk in chunks for x in chunk]))
    return _from_series(series)


def _all_str(df):
    """0.1.x 的輸出格式：每一格都轉成字串（NULL 是 'None'，數值欄的 NULL 是 'nan'）。"""
    return _from_series([pd.Series([str(x) for x in df[i]], dtype=object) for i in df.columns])


class RedshiftClient:
    """
    可重複使用的 Redshift 連線，只需要 JDBC 連線 URL。

    第一次查詢時才連線；同一個 client 的多次查詢共用一條連線，
    使用瀏覽器登入（例如 Azure OAuth2）時只需要登入一次。

    jdbc_url: JDBC 連線 URL
    jvm_path: JVM 路徑；省略時自動偵測
    jars: 自訂的驅動 jar 清單；省略時使用套件內建的驅動
    """

    def __init__(self, jdbc_url, jvm_path=None, jars=None):
        self.jdbc_url = jdbc_url
        self._jvm_path = jvm_path
        self._jars = list(jars) if jars else bundled_jars()
        self._conn = None

    def connect(self):
        # 連線可能已被伺服器中斷（閒置過久、登入逾期）；失效就重新連線
        if self._conn is not None and not self._conn.jconn.isValid(5):
            self._conn = None
        if self._conn is None:
            _start_jvm(self._jvm_path, self._jars)
            self._conn = jaydebeapi.connect(DRIVER_CLASS, self.jdbc_url, [], self._jars)
        return self._conn

    def query(self, query, chunk_size=None, verbose=False, all_str=False):
        """
        執行查詢並返回 DataFrame。

        query: 要執行的 SQL 查詢語句
        chunk_size: 分批讀取的筆數；資料量大時可降低記憶體用量，省略則一次取回
        verbose: 是否印出已讀取的筆數
        all_str: True 時所有欄位都轉成字串（0.1.x 的行為）

        數值欄位為數值型別（NULL 為 NaN）；其餘欄位為去除前後空白的字串（NULL 為 None）。
        結果不受 pandas 版本、是否分批讀取影響。
        """
        cursor = self.connect().cursor()
        try:
            cursor.execute(query)
            if not cursor.description:
                return pd.DataFrame()

            # 獲取列名並去除空格
            columns = [str(desc[0]).strip() for desc in cursor.description]

            if not chunk_size:
                df = _to_dataframe(cursor.fetchall(), len(columns))
            else:
                parts, n = [], 0
                while True:
                    rows = cursor.fetchmany(chunk_size)
                    if not rows:
                        break
                    parts.append(_to_dataframe(rows, len(columns)))
                    n += len(rows)
                    if verbose:
                        print(f"已取 {n:,} 筆", flush=True)
                df = _concat_chunks(parts) if parts else _to_dataframe([], len(columns))

            if all_str:
                df = _all_str(df)
            df.columns = columns
            return df
        finally:
            cursor.close()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def query_redshift(jdbc_url, query, **kwargs):
    """
    連線、執行一次查詢、關閉連線，返回 DataFrame。

    jdbc_url: JDBC 連線 URL
    query: 要執行的 SQL 查詢語句
    其餘參數同 RedshiftClient.query
    """
    with RedshiftClient(jdbc_url) as client:
        return client.query(query, **kwargs)


def fetch_data_from_redshift(jvm_path, jdbc_driver_path, aws_sdk_path, jdbc_url, query):
    """
    0.1.x 的舊介面，保留給既有程式使用；回傳的所有欄位都是字串。

    jvm_path: JVM 路徑（可傳 None，自動偵測）
    jdbc_driver_path: Redshift JDBC 驅動路徑（可傳 None，使用內建驅動）
    aws_sdk_path: AWS SDK 路徑（可傳 None，已不需要）
    jdbc_url: JDBC 連線 URL
    query: 要執行的 SQL 查詢語句
    """
    jars = [p for p in (jdbc_driver_path, aws_sdk_path) if p] if jdbc_driver_path else None
    with RedshiftClient(jdbc_url, jvm_path=jvm_path, jars=jars) as client:
        return client.query(query, all_str=True)
