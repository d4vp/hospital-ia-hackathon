"""Run the ETL from the command line (same code as POST /api/load-data).

Usage (inside backend/):  python -m app.scripts.load_data
Reads DATA_DIR/EXCEL_FILENAME (default app/data/DateBaseHIS.xlsx).
"""
import json

from app.core.logging_config import configure_logging
from app.services.data_loader import load_excel_to_mongo

if __name__ == "__main__":
    configure_logging()
    print(json.dumps(load_excel_to_mongo(), indent=2, default=str, ensure_ascii=False))
