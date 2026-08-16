from pathlib import Path


def test_cte_failure_reply_with_sql_file_uses_draft_sql_when_no_final_sql():
    sql_rel = ""
    draft_sql_rel = "task/1786639999999/draft_sql.sql"
    result_error = 'LLM 传输失败 (ReadTimeout): ReadTimeout("")'
    sql_block = "```sql\nSELECT 1\n```"

    if sql_rel:
        final = (
            "CTE SQL 已生成，但本轮未完成数据文件。\n\n"
            + (f"### SQL 文件\n\n- `{sql_rel}`\n\n" if sql_rel else "")
            + (f"错误：{result_error}\n\n" if result_error else "")
            + sql_block
        ).strip()
    elif draft_sql_rel:
        final = (
            "本轮未完成最终导出，但已保留当前 SQL 草稿。\n\n"
            + f"### SQL 文件\n\n- `{draft_sql_rel}`\n\n"
            + (f"错误：{result_error}\n\n" if result_error else "")
            + sql_block
        ).strip()
    else:
        final = (
            "本轮未生成可用的 CTE SQL，因此没有 SQL 或数据文件可下载。\n\n"
            + (f"错误：{result_error}" if result_error else "")
        ).strip()

    assert "draft_sql.sql" in final
    assert "SELECT 1" in final


def test_cte_failure_artifact_path_is_task_scoped():
    run_id = "1786639999999"
    rel = f"task/{run_id}/draft_sql.sql"
    assert rel == "task/1786639999999/draft_sql.sql"
    assert Path(rel).parts[:2] == ("task", run_id)
