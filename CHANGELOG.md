# Changelog

## 0.2.0

安裝與使用大幅簡化：只需要 `pip install` 與一個 JDBC URL。

### 新增

- 內建 AWS 官方 Redshift JDBC 驅動 2.1.0.33 與其相依 jar，不必再另外下載
- 自動偵測 Java，不必再傳 `jvm.dll` 的路徑
- `RedshiftClient`：多次查詢共用一條連線，瀏覽器登入只需要一次
- `query_redshift(jdbc_url, query)`：一行完成查詢
- `chunk_size` 分批讀取大量資料
- 查詢結果的欄位型別固定：不受 pandas 2 / 3 與是否分批讀取影響，重複的欄名也會保留
- 連線被伺服器中斷時，下一次查詢自動重新連線
- `python -m redshift_connector_bade`：安裝自我檢查，可加上 JDBC URL 測試連線
- `install.bat`：Windows 一鍵安裝
- 自動化測試與 GitHub Actions（測試、建立 Release、上傳 PyPI）

### 變更

- `RedshiftClient.query` 與 `query_redshift` 會保留數值與 `None`；需要全部轉成字串時傳入 `all_str=True`
- 不再需要 `aws-java-sdk-bundle`（94 MB）
- 移除沒有用到的相依套件 `requests`
- 最低支援 Python 3.8

### 相容性

- `fetch_data_from_redshift(jvm_path, jdbc_driver_path, aws_sdk_path, jdbc_url, query)` 維持原本的參數與回傳格式；三個路徑都可以傳 `None`

## 0.1.x

- 初版：`fetch_data_from_redshift`，需要自行下載驅動並傳入各檔案路徑
