"""
生成合工大官网信息扫描报告 HTML（商业级 v2.0）
新增：栏目统计图表、分页、时间线、响应式设计
"""
import sys, io, json, os
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hfut_full_index.json")
OUTPUT_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hfut_report.html")
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hfut_scan_state.json")

with open(INDEX_FILE, "r", encoding="utf-8") as f:
    items = json.load(f)

# 加载扫描状态
state = {}
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

# 过滤近两年的数据
items_filtered = [i for i in items if i.get("date", "")[:4] >= "2024"]

# 统计
channel_count = {}
type_count = {}
date_count = {}
channel_items = {}

for item in items_filtered:
    ch = item.get("channel", "未知")
    tp = item.get("type", "未知")
    dt = item.get("date", "无日期")
    channel_count[ch] = channel_count.get(ch, 0) + 1
    type_count[tp] = type_count.get(tp, 0) + 1
    if ch not in channel_items:
        channel_items[ch] = []
    channel_items[ch].append(item)
    if dt and len(dt) >= 7:
        month = dt[:7]
        date_count[month] = date_count.get(month, 0) + 1

# 按时间排序月份
sorted_months = sorted(date_count.keys(), reverse=True)[:12]

high_priority = [i for i in items_filtered if i.get("score", 0) >= 10]
medium_priority = [i for i in items_filtered if 5 <= i.get("score", 0) < 10]

# 最近7天高相关度
from datetime import timedelta
now = datetime.now()
week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
recent_high = [i for i in high_priority if i.get("date", "") >= week_ago]

# 类型配色
type_colors = {
    "notice": "#3b82f6", "activity": "#f59e0b", "academic": "#8b5cf6",
    "competition": "#ef4444", "news": "#10b981",
}
type_names = {
    "notice": "通知公告", "activity": "校园活动", "academic": "学术科研",
    "competition": "竞赛信息", "news": "新闻动态",
}
type_icons = {
    "notice": "📋", "activity": "🎉", "academic": "🔬",
    "competition": "🏆", "news": "📰",
}

def escape(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

# 生成栏目统计图（CSS柱状图）
max_ch_count = max(channel_count.values()) if channel_count else 1
channel_bars = ""
for ch, cnt in sorted(channel_count.items(), key=lambda x: x[1], reverse=True):
    pct = int(cnt / max_ch_count * 100)
    channel_bars += f'<div class="bar-row"><span class="bar-label">{escape(ch)}</span><div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div><span class="bar-num">{cnt}</span></div>\n'

# 生成时间线图（最近12个月）
timeline_bars = ""
max_dt_count = max(date_count.values()) if date_count else 1
for month in sorted_months:
    cnt = date_count.get(month, 0)
    pct = int(cnt / max_dt_count * 100)
    timeline_bars += f'<div class="tl-col"><div class="tl-bar" style="height:{max(pct, 4)}%" title="{month}: {cnt}条"></div><div class="tl-label">{month[5:]}</div></div>\n'

# 生成条目HTML
items_html = ""
for idx, item in enumerate(items_filtered):
    score = item.get("score", 0)
    if score >= 10:
        score_class = "score-high"
        score_text = "高"
    elif score >= 5:
        score_class = "score-medium"
        score_text = "中"
    else:
        score_class = "score-low"
        score_text = "低"
    
    tp = item.get("type", "notice")
    tag_color = type_colors.get(tp, "#64748b")
    tag_name = type_names.get(tp, tp)
    tag_icon = type_icons.get(tp, "📄")
    
    title = escape(item.get("title", "无标题"))
    date = item.get("date", "")
    channel = escape(item.get("channel", ""))
    url = item.get("url", "#")
    
    # 判断是否最近7天
    is_recent = date >= week_ago
    
    items_html += f"""
        <a class="notice-item{' new-item' if is_recent else ''}" href="{url}" target="_blank" 
           data-type="{tp}" data-score="{score}" data-title="{title}" data-channel="{channel}">
            <div class="score {score_class}" title="相关度: {score}分">{score_text}</div>
            <div class="content">
                <div class="title">{title}</div>
                <div class="meta">{tag_icon} {channel}</div>
            </div>
            <span class="tag" style="background:{tag_color}18;color:{tag_color};border:1px solid {tag_color}40">{tag_name}</span>
            <span class="date">{date}{' 🆕' if is_recent else ''}</span>
        </a>"""

gen_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
last_scan = state.get("last_scan_time", "首次扫描")
total_all = len(items)
total_filtered = len(items_filtered)

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>合工大官网信息扫描报告</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
    background:#0a0f1a;color:#e2e8f0;line-height:1.6;
}}
.container{{max-width:1280px;margin:0 auto;padding:20px}}

/* 头部 */
.header{{
    text-align:center;padding:48px 24px 36px;
    background:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#0f172a 100%);
    border-radius:20px;margin-bottom:28px;
    border:1px solid rgba(99,102,241,0.15);
    position:relative;overflow:hidden;
}}
.header::before{{
    content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;
    background:radial-gradient(circle at 30% 50%,rgba(99,102,241,0.08) 0%,transparent 50%),
               radial-gradient(circle at 70% 50%,rgba(168,85,247,0.06) 0%,transparent 50%);
}}
.header h1{{font-size:32px;font-weight:800;color:#f8fafc;margin-bottom:6px;position:relative;letter-spacing:1px}}
.header .subtitle{{color:#94a3b8;font-size:15px;position:relative}}
.header .time{{color:#475569;font-size:13px;margin-top:8px;position:relative}}
.header .badge{{display:inline-block;padding:4px 14px;background:rgba(16,185,129,0.15);color:#10b981;border-radius:20px;font-size:12px;font-weight:600;margin-top:10px;position:relative}}

/* 统计卡片 */
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:28px}}
.stat-card{{
    background:linear-gradient(135deg,#1e293b,#1a2332);border-radius:14px;padding:22px 20px;
    border:1px solid #2d3a4d;position:relative;overflow:hidden;
}}
.stat-card::after{{content:'';position:absolute;top:0;right:0;width:80px;height:80px;border-radius:50%;opacity:0.04}}
.stat-card .number{{font-size:40px;font-weight:800;letter-spacing:-1px}}
.stat-card .label{{font-size:13px;color:#64748b;margin-top:4px;font-weight:500}}
.stat-card.c1 .number{{color:#f59e0b}} .stat-card.c1::after{{background:#f59e0b}}
.stat-card.c2 .number{{color:#ef4444}} .stat-card.c2::after{{background:#ef4444}}
.stat-card.c3 .number{{color:#8b5cf6}} .stat-card.c3::after{{background:#8b5cf6}}
.stat-card.c4 .number{{color:#10b981}} .stat-card.c4::after{{background:#10b981}}
.stat-card.c5 .number{{color:#3b82f6}} .stat-card.c5::after{{background:#3b82f6}}

/* 图表区 */
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:28px}}
.chart-box{{
    background:#1e293b;border-radius:14px;padding:20px 24px;
    border:1px solid #2d3a4d;
}}
.chart-box h3{{font-size:15px;font-weight:600;color:#94a3b8;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid #2d3a4d}}
.bar-row{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
.bar-label{{min-width:120px;font-size:12px;color:#94a3b8;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.bar-track{{flex:1;height:22px;background:#0f172a;border-radius:6px;overflow:hidden}}
.bar-fill{{height:100%;background:linear-gradient(90deg,#6366f1,#8b5cf6);border-radius:6px;transition:width 0.5s ease;min-width:2px}}
.bar-num{{min-width:28px;font-size:13px;font-weight:700;color:#e2e8f0;text-align:right}}

/* 时间线 */
.timeline{{display:flex;align-items:flex-end;gap:6px;height:160px;padding:10px 0}}
.tl-col{{flex:1;display:flex;flex-direction:column;align-items:center;height:100%}}
.tl-bar{{width:100%;background:linear-gradient(to top,#6366f1,#a78bfa);border-radius:4px 4px 0 0;transition:height 0.3s;min-height:4px}}
.tl-label{{font-size:11px;color:#64748b;margin-top:6px}}

/* 工具栏 */
.toolbar{{
    display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px;
    padding:16px 20px;background:#1e293b;border-radius:14px;border:1px solid #2d3a4d;
}}
.search-box{{flex:1;min-width:240px;position:relative}}
.search-box input{{
    width:100%;padding:10px 14px 10px 38px;
    background:#0f172a;border:1px solid #2d3a4d;border-radius:10px;
    color:#e2e8f0;font-size:14px;outline:none;transition:border-color 0.2s;
}}
.search-box input:focus{{border-color:#6366f1;box-shadow:0 0 0 3px rgba(99,102,241,0.1)}}
.search-box .icon{{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:#475569;font-size:14px}}

.filter-btn{{
    padding:8px 16px;border-radius:10px;border:1px solid #2d3a4d;
    background:transparent;color:#94a3b8;cursor:pointer;
    font-size:13px;transition:all 0.2s;font-weight:500;
}}
.filter-btn:hover{{border-color:#6366f1;color:#e2e8f0;background:#6366f10d}}
.filter-btn.active{{background:#6366f1;border-color:#6366f1;color:white}}

/* 列表 */
.list-header{{
    display:flex;justify-content:space-between;align-items:center;
    padding:12px 20px;margin-bottom:8px;
}}
.list-header .count{{font-size:13px;color:#64748b}}
.list-header .sort-btn{{font-size:12px;color:#6366f1;cursor:pointer;background:none;border:none}}

.notice-list{{display:flex;flex-direction:column;gap:6px;margin-bottom:28px}}
.notice-item{{
    display:flex;align-items:center;gap:14px;
    padding:14px 18px;background:#1e293b;border-radius:12px;
    border:1px solid #2d3a4d;transition:all 0.2s;
    cursor:pointer;text-decoration:none;color:inherit;
}}
.notice-item:hover{{border-color:#6366f1;background:#1a2744;transform:translateX(4px);box-shadow:0 4px 20px rgba(99,102,241,0.08)}}
.notice-item.new-item{{border-left:3px solid #10b981}}
.notice-item .score{{
    min-width:36px;height:36px;border-radius:10px;
    display:flex;align-items:center;justify-content:center;
    font-size:12px;font-weight:700;flex-shrink:0;
}}
.score-high{{background:rgba(239,68,68,0.12);color:#ef4444;border:1px solid rgba(239,68,68,0.2)}}
.score-medium{{background:rgba(245,158,11,0.12);color:#f59e0b;border:1px solid rgba(245,158,11,0.2)}}
.score-low{{background:rgba(100,116,139,0.12);color:#475569;border:1px solid rgba(100,116,139,0.2)}}
.notice-item .content{{flex:1;min-width:0}}
.notice-item .title{{font-size:14px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#cbd5e1}}
.notice-item .meta{{font-size:12px;color:#475569;margin-top:3px}}
.notice-item .tag{{
    padding:4px 10px;border-radius:8px;font-size:11px;font-weight:600;
    flex-shrink:0;letter-spacing:0.3px;
}}
.notice-item .date{{font-size:12px;color:#64748b;flex-shrink:0;min-width:90px;text-align:right;font-variant-numeric:tabular-nums}}

/* 分页 */
.pagination{{
    display:flex;justify-content:center;gap:6px;padding:20px;
}}
.page-btn{{
    padding:8px 14px;border-radius:8px;border:1px solid #2d3a4d;
    background:#1e293b;color:#94a3b8;cursor:pointer;font-size:13px;transition:all 0.2s;
}}
.page-btn:hover{{border-color:#6366f1;color:#e2e8f0}}
.page-btn.active{{background:#6366f1;border-color:#6366f1;color:white}}
.page-btn.disabled{{opacity:0.3;cursor:default}}

/* 页脚 */
.footer{{
    text-align:center;padding:30px 20px;color:#334155;font-size:12px;
    border-top:1px solid #1e293b;margin-top:20px;
}}

/* 响应式 */
@media(max-width:768px){{
    .charts{{grid-template-columns:1fr}}
    .bar-label{{min-width:80px;font-size:11px}}
    .stats{{grid-template-columns:repeat(2,1fr)}}
    .toolbar{{flex-direction:column}}
}}

.empty{{text-align:center;padding:60px 20px;color:#475569}}
.empty .icon{{font-size:48px;margin-bottom:12px}}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>合工大官网信息扫描报告</h1>
        <div class="subtitle">覆盖 {len(channel_count)} 个栏目 · 智能筛选高相关度信息 · 自动化每日巡检</div>
        <div class="time">报告生成：{gen_time} ｜ 上次扫描：{last_scan}</div>
        <div class="badge">{'🆕 近7天新增 ' + str(len(recent_high)) + ' 条高相关度' if recent_high else '✓ 系统运行正常'}</div>
    </div>
    
    <div class="stats">
        <div class="stat-card c1"><div class="number">{total_filtered}</div><div class="label">信息条目（近2年）</div></div>
        <div class="stat-card c2"><div class="number">{len(high_priority)}</div><div class="label">高相关度</div></div>
        <div class="stat-card c3"><div class="number">{len(medium_priority)}</div><div class="label">中等相关度</div></div>
        <div class="stat-card c4"><div class="number">{len(channel_count)}</div><div class="label">覆盖栏目</div></div>
        <div class="stat-card c5"><div class="number">{len(recent_high)}</div><div class="label">近7天新增</div></div>
    </div>
    
    <div class="charts">
        <div class="chart-box">
            <h3>📊 栏目信息分布</h3>
            {channel_bars}
        </div>
        <div class="chart-box">
            <h3>📅 月度发布趋势（近12个月）</h3>
            <div class="timeline">{timeline_bars}</div>
        </div>
    </div>
    
    <div class="toolbar">
        <div class="search-box">
            <span class="icon">🔍</span>
            <input type="text" id="search" placeholder="搜索通知标题、栏目..." oninput="filterList()">
        </div>
        <button class="filter-btn active" onclick="setFilter('all',this)">全部 ({total_filtered})</button>
        <button class="filter-btn" onclick="setFilter('high',this)">🔥 高相关度 ({len(high_priority)})</button>
        <button class="filter-btn" onclick="setFilter('recent',this)">🆕 近7天</button>
        {" ".join(f'<button class="filter-btn" onclick="setFilter(\'{k}\',this)">{type_icons.get(k,"")} {type_names.get(k,k)} ({type_count.get(k,0)})</button>' for k in sorted(type_count.keys()))}
    </div>
    
    <div class="list-header">
        <span class="count" id="visibleCount">显示 {total_filtered} 条</span>
        <button class="sort-btn" onclick="toggleSort()">📅 按日期排序</button>
    </div>
    
    <div class="notice-list" id="noticeList">{items_html}</div>
    <div class="pagination" id="pagination"></div>
    
    <div class="empty" id="emptyMsg" style="display:none">
        <div class="icon">🔎</div>
        <div>没有匹配的通知</div>
        <div style="margin-top:8px;font-size:12px">尝试更换关键词或筛选条件</div>
    </div>
    
    <div class="footer">
        合肥工业大学官网信息自动巡检系统 v2.0 · 由 AI 驱动 · 数据来源于学校官网公开信息
    </div>
</div>

<script>
const ITEMS_PER_PAGE = 30;
let currentFilter = 'all';
let currentPage = 1;
let sortAsc = false;
let allItems = document.querySelectorAll('.notice-item');
let weekAgo = '{week_ago}';

function setFilter(type, btn) {{
    currentFilter = type;
    currentPage = 1;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    if(btn) btn.classList.add('active');
    filterList();
}}

function filterList() {{
    const search = document.getElementById('search').value.toLowerCase();
    let visible = [];
    
    allItems.forEach(item => {{
        const type = item.dataset.type;
        const score = parseInt(item.dataset.score);
        const title = item.dataset.title.toLowerCase();
        const channel = item.dataset.channel.toLowerCase();
        
        let showByFilter = true;
        if(currentFilter === 'high') showByFilter = score >= 10;
        else if(currentFilter === 'recent') showByFilter = item.dataset.score !== undefined; // handled below
        else if(currentFilter !== 'all') showByFilter = type === currentFilter;
        
        let showBySearch = !search || title.includes(search) || channel.includes(search);
        
        let show = showByFilter && showBySearch;
        
        // 近7天筛选
        if(currentFilter === 'recent') {{
            const dateEl = item.querySelector('.date');
            const dateStr = dateEl ? dateEl.textContent.replace(/\\s/g, '').trim() : '';
            show = showBySearch && dateStr >= weekAgo;
        }}
        
        item.style.display = show ? 'flex' : 'none';
        if(show) visible.push(item);
    }});
    
    document.getElementById('visibleCount').textContent = '显示 ' + visible.length + ' 条';
    document.getElementById('emptyMsg').style.display = visible.length === 0 ? 'block' : 'none';
    
    paginate(visible);
}}

function paginate(visibleItems) {{
    const total = visibleItems.length;
    const totalPages = Math.ceil(total / ITEMS_PER_PAGE);
    if(currentPage > totalPages) currentPage = 1;
    
    let shown = 0;
    visibleItems.forEach((item, i) => {{
        if(i >= (currentPage - 1) * ITEMS_PER_PAGE && i < currentPage * ITEMS_PER_PAGE) {{
            item.style.display = 'flex';
            shown++;
        }} else {{
            item.style.display = 'none';
        }}
    }});
    
    // 分页按钮
    const pag = document.getElementById('pagination');
    if(totalPages <= 1) {{ pag.innerHTML = ''; return; }}
    
    let html = '';
    html += '<button class="page-btn' + (currentPage<=1?' disabled':'') + '" onclick="goPage('+(currentPage-1)+')">上一页</button>';
    for(let p=1;p<=totalPages&&p<=10;p++) {{
        html += '<button class="page-btn'+(p===currentPage?' active':'')+'" onclick="goPage('+p+')">'+p+'</button>';
    }}
    if(totalPages>10) html += '<span style="color:#475569;line-height:36px">...</span>';
    html += '<button class="page-btn' + (currentPage>=totalPages?' disabled':'') + '" onclick="goPage('+(currentPage+1)+')">下一页</button>';
    pag.innerHTML = html;
}}

function goPage(p) {{
    currentPage = p;
    filterList();
    window.scrollTo({{top: document.getElementById('noticeList').offsetTop - 20, behavior: 'smooth'}});
}}

function toggleSort() {{
    sortAsc = !sortAsc;
    const list = document.getElementById('noticeList');
    const items = Array.from(list.children);
    items.sort((a,b) => {{
        const da = a.querySelector('.date').textContent.replace(/\\s/g,'');
        const db = b.querySelector('.date').textContent.replace(/\\s/g,'');
        return sortAsc ? da.localeCompare(db) : db.localeCompare(da);
    }});
    items.forEach(item => list.appendChild(item));
    allItems = document.querySelectorAll('.notice-item');
    filterList();
}}

// 初始化
filterList();
</script>
</body>
</html>"""

with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)

print(f"报告已生成: {OUTPUT_HTML}")
print(f"总条目: {total_all}, 近两年: {total_filtered}")
print(f"高相关度: {len(high_priority)}, 近7天新增: {len(recent_high)}")
print(f"覆盖栏目: {len(channel_count)}")
