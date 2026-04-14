# 每日卡片 (Daily Card) v1.6.0
### **提醒:请先安装前置插件`极趣AI便利贴(Zectrix Todo)`**
下载地址:https://github.com/zhangkai542322/astrbot_plugin_zectrix

LLM驱动的每日卡片图片生成插件 — **21款模板** · **400×300纯黑白** · **自带中文字体**

全部功能通过与AI对话驱动。图片生成后可由 LLM 调用 [**极趣AI便利贴(Zectrix Todo)**](https://github.com/zhangkai542322/astrbot_plugin_zectrix) 插件推送到墨水屏设备。

## 功能

| 对话示例 | 本插件工具 | 后续可链式调用 |
|---------|-----------|--------------|
| "看看北京天气" | `get_weather_card` | → `push_image_to_device`(Zectrix) |
| "看看今天的待办" | `get_schedule_card` | → `push_image_to_device`(Zectrix) |
| "今天有什么新闻" | `get_news_card` | → `push_image_to_device`(Zectrix) |
| "做个早安图片" | `get_custom_card` | → `push_image_to_device`(Zectrix) |
| "把天气和待办合成一张图" | `get_combined_card` | → `push_image_to_device`(Zectrix) |
| "有哪些模板" | `list_card_templates` | — |

## 建议

将这段提示词加入Astrbot人格提示中

```markdown
##每日卡片工具使用规范##

当用户请求以下操作时，调用对应工具生成图片：

1. **查天气/天气怎么样** → `get_weather_card(city="城市名")`
2. **生成日程图/今日日程** → `get_schedule_card(data_json=..., template="schedule_grid")`
3. **课程表** → `get_schedule_card(data_json=..., template="course_table")`
4. **进度图/进度概览** → `get_schedule_card(data_json=..., template="progress")`
5. **今日新闻/新闻摘要** → `get_news_card(data_json=..., template="headline")`
6. **生成贺卡/早安问候** → `get_custom_card(data_json=..., template="greeting")`
7. **名言警句** → `get_custom_card(data_json=..., template="quote")`
8. **备忘录** → `get_custom_card(data_json=..., template="memo")`
9. **生成汇总图/今日总览** → `get_combined_card(data_json=..., template="daily_summary")`

**重要规则：**

- 工具只负责**生成图片**
- 用户提出"发送""消息"时,将生成好的图片发送给用户
- 只有当用户明确说"**同步**"、"**推送**"、"**更新**"、"发送到设备/第X页"等时才调用 `push_image_to_device`
- **禁止**使用 `astrbot_execute_python` 或其他方式生成中文图片（字体可能不支持）
```



## 工作流

```
用户: "把北京天气推送到设备"
  ↓
LLM 调用 get_weather_card → 生成图片，返回路径
  ↓
LLM 调用 Zectrix 插件的 push_image_to_device(image_path=路径) → 推送到设备
```

## 21款模板

**天气×8:** classic, newspaper, dashboard, minimal, datapanel, timeline, postcard, terminal
**日程×3:** schedule_grid, course_table, progress
**新闻×2:** headline, ticker
**自定义×4:** quote, memo, greeting, list
**聚合×2:** daily_summary, split_panel

## 模板参数说明

### get_weather_card
```
【通用必填】city: 城市名称, temp: 当前温度, desc: 天气描述
【通用选填】feels_like: 体感温度, humidity: 湿度(%), wind_dir: 风向(0-7), wind_speed: 风速(km/h)
【未来预报】daily: [{"weekday":"周一","max":25,"min":15,"desc":"晴","date":"2026-04-14"}]
【逐时预报】hourly: [{"time":"08:00","temp":20}]
【数据面板额外】uv: 紫外指数, clothing: 穿衣指数, aqi: 空气质量
```
模板: classic(经典简约), newspaper(报纸风格), dashboard(仪表盘), minimal(极简主义), datapanel(数据面板), timeline(时间轴), postcard(明信片), terminal(终端风格)

### get_schedule_card
```
【schedule_grid 日程表格】title: 标题, date: 日期, weekday: 周几, slots: [{"time":"09:00-10:00","event":"开会","location":"会议室"}]
【course_table 课程表】title: 标题, courses: [{"period":"1","name":"数学","room":"A101","teacher":"张老师","time":"08:00-09:40"}]
【progress 进度概览】title: 标题, total_progress: 总进度(0-100), items: [{"label":"任务名","detail":"描述","progress":90}]
```
模板: schedule_grid(日程表格), course_table(课程表), progress(进度概览)

### get_news_card
```
【headline 头条摘要】title: 标题, date: 日期, category: 分类, items: [{"headline":"标题","summary":"摘要"}]
【ticker 滚动条】title: 标题, date: 日期, items: [{"headline":"标题","tag":"标签"}]
```
模板: headline(头条摘要), ticker(滚动条)

### get_custom_card
```
【quote 名言】quote: 名言内容, author: 作者, date: 日期
【memo 备忘录】title: 标题, content: 内容, tags: ["标签1"], date: 日期
【greeting 贺卡】greeting: 祝福语(必填), message: 详细消息, date: 日期
【list 列表】title: 标题, items: ["项目1","项目2"], date: 日期, footer: 底部备注
```
模板: quote(名言), memo(备忘录), greeting(贺卡), list(列表)

### get_combined_card
```
【daily_summary 每日总览】date: 日期, weather: {"city":"城市","temp":22,"desc":"描述"}, todos: [{"text":"任务","done":false}], news_brief: 新闻内容, quote: 名言
【split_panel 分屏面板】date: 日期, left/center/right: {"title":"标题","lines":["项目1","项目2"]}
```
模板: daily_summary(每日总览), split_panel(分屏面板)

## 配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `default_city` | 北京 | 默认天气查询城市 |
| `default_template` | classic | 默认天气模板 |
| `temp_unit` | celsius | 温度单位 (celsius/fahrenheit) |
| `font_dir` | `/AstrBot/data/fonts` | 字体查找目录 |
| `font_filename` | (空) | 指定字体文件名，留空默认用 font.ttf |

## 字体

本插件自带中文点阵字体Zfull（`assets/font.ttf`），开箱即用，无需额外配置。

### 自定义字体（可选）

#### **注:其他字体大概率会使界面和文字错位,且目前自定义字体功能健壮性差,不建议使用**

如仍需使用其他字体：

1. 将字体文件放到 `/AstrBot/data/fonts/` 目录
2. 用 `/daily_card setfont 文件名` 指定，或在 WebUI → 插件设置 → `font_filename` 中填写
3. 优先级：用户指定 → font.ttf → font_dir 下任意字体 → 插件自带字体
3. 如果字体不生效, 可手动告知AI查找并使用指定字体

### 查看字体状态

```
/daily_card font
```

## 安装

1. 将 `astrbot_plugin_daily_card` 文件夹放入 `data/plugins/`，重启 AstrBot
2. 可配合 **极趣AI便利贴(Zectrix Todo)** 插件推送至设备

## 更新日志

### v1.6.0
- 🔧 统一所有模板的参数文档格式
- 🔧 优化 daily_summary 布局
- 🔧 更改自带字体为Zfull点阵字体,更加适合1bit墨水屏设备
## License

[MIT](LICENSE) © 2026 zhangkai542322
