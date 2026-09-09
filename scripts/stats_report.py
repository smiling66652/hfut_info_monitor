"""生成项目真实运行指标，供 README 引用。避免手写数字。

用法：python scripts/stats_report.py
"""
import json
import os
import sys
import datetime

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "data", "hfut_archive.json")


def load_jsonl(path):
    """归档文件为 JSONL 格式（每行一条），兼容整体为数组的旧格式。"""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(1)
        f.seek(0)
        if head == "[":
            rows = json.load(f)
        else:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return rows


def main():
    rows = load_jsonl(ARCHIVE)
    print("归档条目总数: %d" % len(rows))
    if rows:
        keys = set()
        for r in rows[:50]:
            keys.update(r.keys())
        print("字段: %s" % ", ".join(sorted(keys)))
        for field in ("source", "栏目", "category", "site", "channel"):
            if field in keys:
                vals = {}
                for r in rows:
                    v = r.get(field) or "未知"
                    vals[v] = vals.get(v, 0) + 1
                print("\n按 %s 分布（%d 类）:" % (field, len(vals)))
                for k, v in sorted(vals.items(), key=lambda x: -x[1])[:15]:
                    print("  %-28s %d" % (str(k)[:28], v))
                break
        for field in ("date", "publish_date", "time", "timestamp"):
            if field in keys:
                ds = sorted(str(r.get(field) or "") for r in rows)
                ds = [d for d in ds if d]
                if ds:
                    print("\n日期范围: %s ~ %s" % (ds[0][:10], ds[-1][:10]))
                break
        for field in ("score", "relevance", "relevance_score"):
            if field in keys:
                vals = [r.get(field) for r in rows if isinstance(r.get(field), (int, float))]
                if vals:
                    hit = len([v for v in vals if v >= (max(vals) * 0.5)])
                    print("评分字段 %s: 有值 %d 条，>= 中位门槛 %d 条" % (field, len(vals), hit))
                break

    py_files = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "scripts")):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".venv")]
        for fn in filenames:
            if fn.endswith(".py"):
                py_files.append(os.path.join(dirpath, fn))
    total = sum(sum(1 for _ in open(p, encoding="utf-8", errors="replace")) for p in py_files)
    print("\nscripts 下 py 文件: %d 个，合计 %d 行" % (len(py_files), total))

    top_py = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".venv", "__pycache__", "node_modules", "NapCat", "go-cqhttp", ".git")]
        for fn in filenames:
            if fn.endswith(".py"):
                top_py.append(os.path.join(dirpath, fn))
    tot2 = sum(sum(1 for _ in open(p, encoding="utf-8", errors="replace")) for p in top_py)
    print("项目自研 py 文件: %d 个，合计 %d 行" % (len(top_py), tot2))

    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(ARCHIVE))
    print("归档最后更新: %s" % mtime.strftime("%Y-%m-%d %H:%M"))


if __name__ == "__main__":
    main()
