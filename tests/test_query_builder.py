from query_builder import QueryBuilder


def test_query_builder_expands_suffixes_and_synonyms():
    queries = QueryBuilder(["低空经济"], suffixes=["政策", "公告"]).build()

    assert "低空经济 政策" in queries
    assert "低空经济 公告" in queries
    assert "无人机 政策" in queries
    assert "eVTOL 公告" in queries


def test_query_builder_preserves_order_and_dedupes():
    queries = QueryBuilder(["机器人", "机器人"], suffixes=["风险"]).build()

    assert "机器人" not in queries
    assert queries.count("机器人 风险") == 1
    assert queries[0] == "机器人 风险"
    assert "人形机器人 风险" in queries
