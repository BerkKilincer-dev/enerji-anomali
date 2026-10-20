import pandas as pd

from src.data.split import Split, assign_splits, hashed_split


def test_hashed_split_is_stable_and_roughly_70_15_15():
    names = [hashed_split(f"customer-{i}") for i in range(4000)]
    assert names == [hashed_split(f"customer-{i}") for i in range(4000)]
    share = pd.Series(names).value_counts(normalize=True)
    assert abs(share["train"] - 0.70) < 0.03
    assert abs(share["val"] - 0.15) < 0.03


def test_precedence_explicit_then_stored_then_file_then_hash():
    buildings = pd.DataFrame(
        {
            "building_id": [1, 2, 100000],
            "external_id": ["lead-1", "lead-2", "acme"],
            "split": [None, "val", None],
        }
    )
    committed = Split(train=[1, 2], val=[], test=[])
    assert assign_splits(buildings, committed).tolist()[:2] == ["train", "train"]
    from_file = assign_splits(buildings.assign(split=[None, "val", None]))
    assert from_file.iloc[1] == "val"
    assert from_file.iloc[2] == hashed_split("acme")
