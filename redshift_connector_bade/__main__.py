"""
安裝自我檢查：

    python -m redshift_connector_bade                檢查 Java 與內建驅動
    python -m redshift_connector_bade "<JDBC URL>"   再實際連線並查詢一次

結束代碼：0 正常、1 其他錯誤、2 沒有可用的 Java（未安裝或版本太舊）。
"""

import os
import sys

import jpype

from . import RedshiftClient, __version__
from .connector import DRIVER_CLASS, _start_jvm, bundled_jars, find_jvm


def main(argv):
    # 輸出被導向檔案或管線時，避免非中文語系的主控台編碼錯誤
    if not sys.stdout.isatty() and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print(f"redshift-connector-bade {__version__}")

    jars = bundled_jars()
    driver = [os.path.basename(j) for j in jars if os.path.basename(j).startswith("redshift-jdbc42-")]
    if not driver:
        print("[失敗] 套件內找不到 JDBC 驅動，請重新安裝：pip install --force-reinstall redshift-connector-bade")
        return 1
    print(f"[OK] 內建驅動：{driver[0]}（共 {len(jars)} 個 jar）")

    try:
        jvm_path = find_jvm()
    except RuntimeError as e:
        print(f"[失敗] {e}")
        return 2
    print(f"[OK] Java：{jvm_path}")

    _start_jvm(jvm_path, jars)
    jpype.JClass(DRIVER_CLASS)
    print("[OK] 驅動載入成功")

    if len(argv) < 2:
        print('安裝正常。要連同連線一起測試：python -m redshift_connector_bade "<JDBC URL>"')
        return 0

    print("連線測試中（使用瀏覽器登入時，請在跳出的視窗完成登入）...", flush=True)
    with RedshiftClient(argv[1]) as client:
        df = client.query("select current_user as login_user, current_date as today")
    print(df.to_string(index=False))
    print("[OK] 連線與查詢成功")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
