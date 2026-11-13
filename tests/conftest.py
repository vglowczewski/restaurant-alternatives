import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DIRECTORY_CSV = """nyc_id,name,address,zip,lat,lon,cuisine,cuisine_family
1,DON ANGIE,103 GREENWICH AVENUE,10014,40.7377,-74.0018,Italian,Italian/Pizza
2,DON ANGIE,235 WEST 12 STREET,10014,40.7376,-74.0022,Italian,Italian/Pizza
3,ANGIE'S PIZZA,10 MAIN STREET,10001,,,Pizza,Italian/Pizza
4,CAFÉ BORÉAL,5 PARK AVENUE,10016,40.7450,-73.9810,French,European
5,SUSHI ZEN,108 WEST 44 STREET,10036,40.7560,-73.9840,Japanese,East Asian
6,"JOE'S SHANGHAI, INC.",24 PELL STREET,10013,40.7146,-73.9981,Chinese,East Asian
"""


@pytest.fixture
def directory_csv(tmp_path):
    p = tmp_path / "directory.csv"
    p.write_text(DIRECTORY_CSV, encoding="utf-8")
    return p


@pytest.fixture
def directory(directory_csv):
    from data_store import load_directory

    return load_directory(directory_csv)


@pytest.fixture
def aliases():
    return pd.DataFrame(
        [{"alias": "The Shanghai Joe", "nyc_id": "6", "source": "manual"}],
        columns=["alias", "nyc_id", "source"],
    )
