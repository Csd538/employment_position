from sqlalchemy import text
from employment_position.util.config import POSITION_TABLE_NAME
from employment_position.util.database import create_database_engine, load_active_positions

engine = create_database_engine()
try:
    targets = load_active_positions(engine)
    with engine.connect() as connection:
        expected = list(connection.execute(text(f"SELECT position_id FROM `{POSITION_TABLE_NAME}` WHERE is_active != 0 AND position_id IS NOT NULL AND TRIM(CAST(position_id AS CHAR)) != '' ORDER BY id ASC LIMIT 10")).scalars())
    actual = [target.bcb009 for target in targets[:10]]
    assert actual == [str(value).strip() for value in expected], (actual, expected)
    print({"total_targets": len(targets), "first_10_follow_id_asc": True, "first_position_id": actual[0]})
finally:
    engine.dispose()