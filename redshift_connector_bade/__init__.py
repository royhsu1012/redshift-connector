# redshift_connector_bade/__init__.py

"""
redshift_connector_bade
一個簡單的 Amazon Redshift JDBC 查詢工具
"""

from .connector import RedshiftClient, query_redshift, fetch_data_from_redshift

__version__ = "0.2.0"

__all__ = ["RedshiftClient", "query_redshift", "fetch_data_from_redshift"]
