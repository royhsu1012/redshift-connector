# redshift-connector-bade

用 Python 查詢 Amazon Redshift，回傳 pandas DataFrame。JDBC 驅動已內建，只需要一個 JDBC 連線 URL。

*Query Amazon Redshift from Python over JDBC and get a pandas DataFrame. The official driver is bundled, so the JDBC URL is the only thing you configure.*

```python
from redshift_connector_bade import RedshiftClient

rs = RedshiftClient("jdbc:redshift://<host>:5439/<database>?...")
df = rs.query("select * from my_table limit 10")
```

## 為什麼需要這個

公司的 Redshift 常用瀏覽器單一登入（例如 Azure AD OAuth2）。這類登入由 **JDBC 驅動的外掛**處理，所以即使是寫 Python，也得走 JDBC。直接這樣做，每個人、每台電腦、每個專案都要重複一串手工步驟：

| | 以前（0.1.x 或自己寫） | 現在（0.2.0） |
|---|---|---|
| Java | 要安裝，還要自己找出 `jvm.dll` 的完整路徑寫進程式；Java 一更新路徑就失效 | 要安裝，路徑自動偵測 |
| JDBC 驅動 | 到 AWS 網站下載 zip、解壓縮、放到固定資料夾 | 已內建在套件裡 |
| AWS SDK | 另外找一個 94 MB 的 `aws-java-sdk-bundle` | 不需要 |
| 專案內容 | 每個專案放一份 100 MB 的 `drivers/` 資料夾 | 不用放 |
| 程式碼 | 每支程式貼 30 行 `jpype` / `jaydebeapi` 樣板，傳 5 個參數 | 2 行，傳 1 個 URL |
| 瀏覽器登入 | 每查詢一次就登入一次 | 同一個 client 只登入一次 |
| 換電腦 | 全部重來 | 執行 `install.bat` |

那個 94 MB 的 `aws-java-sdk-bundle` 其實從來就不需要：AWS 官方驅動 zip 內附的 15 個小 jar（合計約 8 MB）已經包含驅動需要的全部相依元件。本套件內建的就是這份官方 zip 的原始內容。

## 安裝

需要 Python 3.8 以上與 Java。Java 只要裝好即可，不必設定路徑；電腦上有多個版本時會自動選用相容的那一個。

> **只有 Java 8 的電腦**：新版的 JPype（1.7 起）需要 Java 9 以上。執行 `install.bat` 會自動加裝新版 Java，舊版可以保留；不想加裝的話，改裝支援 Java 8 的 JPype 也可以：`pip install jpype1==1.5.2`。

### 一鍵安裝（Windows）

下載 [`install.bat`](https://github.com/royhsu1012/redshift-connector/releases/latest/download/install.bat) 後雙擊。它會：

1. 安裝或升級本套件
2. 檢查 Java 與內建驅動
3. 沒有 Java、或 Java 版本太舊時，用 `winget` 安裝 Eclipse Temurin JRE 21（原有的 Java 不會被移除）

套件會裝進目前使用中的 Python。要裝進特定的 conda 環境或 venv，請先啟用該環境，再從那個命令列執行 `install.bat`。

從網路下載的 `.bat`，Windows 可能會顯示「Windows 已保護您的電腦」，按「其他資訊」→「仍要執行」即可。安裝 Java 時可能會跳出系統管理員權限的確認視窗。

### 手動安裝

```bash
pip install --upgrade redshift-connector-bade
```

```bash
python -m redshift_connector_bade
```

第二個指令是自我檢查，全部顯示 `[OK]` 就代表可以用了。後面加上 JDBC URL 可以連同連線一起測：

```bash
python -m redshift_connector_bade "jdbc:redshift://<host>:5439/<database>?..."
```

## 使用方式

### 查詢一次

```python
from redshift_connector_bade import query_redshift

df = query_redshift(JDBC_URL, "select count(*) from sales")
```

### 多次查詢共用一條連線

第一次查詢時才連線，之後共用同一條連線。使用瀏覽器登入時只會跳出一次登入視窗。

```python
from redshift_connector_bade import RedshiftClient

with RedshiftClient(JDBC_URL) as rs:
    df_sales = rs.query(SALES_SQL)
    df_model = rs.query(MODEL_SQL)
    df_price = rs.query(PRICE_SQL)
```

### 大量資料

`chunk_size` 會分批讀取，降低記憶體用量；`verbose=True` 會印出目前讀到第幾筆。

```python
df = rs.query(SALES_SQL, chunk_size=50_000, verbose=True)
```

### JDBC URL 範例：Azure AD 瀏覽器登入

```python
JDBC_URL = (
    "jdbc:redshift://<cluster>.<region>.redshift.amazonaws.com:5439/<database>"
    "?plugin_name=com.amazon.redshift.plugin.BrowserAzureOAuth2CredentialsProvider"
    "&idp_tenant=<Azure tenant id>"
    "&client_id=<Azure application id>"
    "&scope=api://<Azure application id>/jdbc_login"
    "&listen_port=7890"
    "&idp_response_timeout=50"
    "&ssl=true"
)
```

帳號密碼、IAM 等其他登入方式也都可以，URL 寫法與 DBeaver 等 JDBC 工具相同。

### 回傳的資料

| 欄位內容 | DataFrame 的欄位 |
|---|---|
| 全是數值 | 數值型別，NULL 為 `NaN` |
| 其他（文字、日期、時間） | `object` 型別，內容是去除前後空白的字串，NULL 為 `None` |

同一個查詢的結果固定不變：不受安裝的 pandas 版本（2 或 3）影響，分批讀取與一次讀完也相同。查詢結果有重複的欄名時，各欄都會保留。

傳入 `all_str=True` 會把所有欄位都轉成字串（0.1.x 的行為）。

### 連線中斷

連線閒置過久被伺服器中斷、或登入逾期時，下一次查詢會自動重新連線（使用瀏覽器登入時會再跳出一次登入視窗）。

## 從 0.1.x 升級

舊的寫法不用改，照常運作：

```python
from redshift_connector_bade import fetch_data_from_redshift

df = fetch_data_from_redshift(jvm_path, jdbc_driver_path, aws_sdk_path, jdbc_url, query)
```

三個路徑現在都可以傳 `None`，會改用自動偵測的 Java 與內建驅動。確認新寫法可用之後，就可以刪掉專案裡的 `drivers/` 資料夾與寫死的 `JVM_PATH`。

## 疑難排解

| 訊息 | 處理方式 |
|---|---|
| `找不到 Java` | 執行 `install.bat`，或自行安裝 Java。已安裝卻仍找不到時，設定環境變數 `JAVA_HOME`，或傳入 `RedshiftClient(url, jvm_path=...)` |
| `找到的 Java 是第 8 版，但目前安裝的 JPype … 需要 Java 9 以上` | 執行 `install.bat` 加裝新版 Java（舊版可保留），或 `pip install jpype1==1.5.2` |
| 瀏覽器登入逾時 | 在 `idp_response_timeout` 秒內完成登入；確認 `listen_port` 沒有被其他程式占用 |
| `Connection refused` / 連線逾時 | 確認已連上公司網路或 VPN |
| 想用別的驅動版本 | `RedshiftClient(url, jars=[r"C:\path\redshift-jdbc42-x.y.z.jar"])` |

## 維護

```bash
pip install -e . pytest
```

```bash
pytest
```

測試不需要資料庫：查詢結果的處理邏輯用假連線測，內建驅動則用真的 JVM 載入、連到本機關閉的 port。

### 更新內建的 JDBC 驅動

1. 從 [AWS 官方頁面](https://docs.aws.amazon.com/redshift/latest/mgmt/jdbc20-download-driver.html)下載含相依元件的 zip（`redshift-jdbc42-x.y.z.zip`）
2. 把 `redshift_connector_bade/jars/` 裡的 `.jar` 全部換成 zip 內的 `.jar`
3. 執行 `pytest`。測試會比對驅動 jar 的 manifest，漏放或多放任何 jar 都會失敗
4. 更新 `jars/THIRD_PARTY_NOTICES.txt` 裡的版本號

### 發行新版本

1. 修改 `redshift_connector_bade/__init__.py` 的 `__version__`（版本號只寫在這裡）
2. 在 `CHANGELOG.md` 加上變更內容
3. 合併到 `main` 之後打上同名的 tag，例如 `v0.2.1`，並 push

GitHub Actions 會先跑測試，通過後自動建立 GitHub Release（附上 wheel 與 `install.bat`），並上傳到 PyPI。上傳 PyPI 需要在 repo 設定 `PYPI_USERNAME`（填 `__token__`）與 `PYPI_PASSWORD`（填 PyPI API token）兩個 secret；沒有設定時會略過這一步。

## 授權

本套件以 MIT 授權釋出。內建的 jar 檔各自適用其原始授權，詳見 [`THIRD_PARTY_NOTICES.txt`](redshift_connector_bade/jars/THIRD_PARTY_NOTICES.txt)。
