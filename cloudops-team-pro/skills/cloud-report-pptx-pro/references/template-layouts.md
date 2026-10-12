# VCIC 模板 Layout 速查表

模板：`templates/cloud-report-template.pptx`
尺寸：13.33 × 7.5 in（16:9）
品牌色：Deep Space Blue（深空蓝） + Vivid Green（翠绿）
字体：The Group Head Light（标题） / The Group Text Regular（正文）

## 本 skill 实际使用的 layout

| layout 名称 | 用途 | 关键 placeholder.idx |
|---|---|---|
| `Titelfolie` | 封面页 | 0=Title, 1=Subtitle, 18=Classification |
| `5x Agenda Deep Space Blue` | 5 项目录页 | 0=Title; 14/26/28/30/32=Agenda Item 1-5; 13/25/27/29/31=Numbering 1-5 |
| `Abschnitts-überschrift` | 章节封面 | 0=Title, 1=Subheader, 36=Section Number |
| `Title and 3 Text Boxes, Deep Space Blue` | 三栏要点（亮点页） | 0=Title, 14=Action Title, 39/40/41=三个 Text Placeholder |
| `Titel und Inhalt` | 标题 + 正文（多用于表格/列表） | 0=Title, 14=Action Title, 1=Content Placeholder |
| `Title and Content Deep Space Blue` | 同上深蓝版 | 同上 |
| `Nur Titel` | 仅标题（图表/自定义形状用） | 0=Title, 14=Action Title |
| `Section Header White` | 章节封底/过渡 | 0=Title |

## 模板里其它 layout（暂未使用，备查）

- `Inhalt mit Überschrift`、`Bild mit Überschrift`、`Quote with Picture`、`Title, 2x images and text`、`Title, 3x images and text`、`Title, 4x images and text Deep Space Blue`、`Vergleich`、`Zwei Inhalte`、`Final Slide Deep Space Blue`、`Chart with Caption` 等

> 如果未来需要图文混排或对比页，可以扩展使用上述 layout。

## 颜色十六进制（来自模板主题）

- Deep Space Blue: `#001E50`
- Vivid Green: `#00B0B9`（实际为 Volkswagen Vivid Turquoise，命名沿用模板）
- White: `#FFFFFF`
- Light Grey: `#DFE4E8`

## 字号约定

- 封面主标题：44pt
- 章节标题：28pt
- Action Title（副标题）：18pt
- 正文：14pt
- 表格小字：11-12pt
