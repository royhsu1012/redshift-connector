import os


def pytest_runtest_logreport(report):
    """在 GitHub Actions 上把失敗的測試標成 annotation，不必打開 log 就看得到原因。"""
    if report.failed and os.environ.get("GITHUB_ACTIONS") == "true":
        title = report.nodeid.replace("::", " > ")
        detail = str(report.longrepr)[-3000:]
        detail = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        # 主控台不一定是 UTF-8（Windows runner 是 cp1252），只輸出 ASCII
        print(f"\n::error title={title}::{detail}".encode("ascii", "backslashreplace").decode())
