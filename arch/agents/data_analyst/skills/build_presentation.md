# Build Presentation

Title: Build Presentation

Description: Builds a short presentation (HTML slide deck and PowerPoint .pptx) with charts and key findings from a dataset (CSV, TSV, Excel, or JSON). Use it when the user asks for a presentation, slide deck, slides, PowerPoint, or pptx based on their data. It never changes the source file.

Allowed Paths: ./workspace/projects

## Instructions

You turn a data file into a short, clear presentation that tells one story, backed by exact numbers.

### Rules

1. Never put a number in a finding, chart title, or reply unless it came from a `query_data` or `analyze_data` result in this session. Never estimate, round from memory, or read numbers off a chart.
2. Never put a claim in a chart title unless a tool result supports it. A title such as "South earns the most revenue" must be checked with `query_data` first.
3. Never modify the source data file. The presentation is always new files.
4. Stay inside `./workspace/projects`. If the user points to a file outside this path, say so and ask for a file inside it.
5. Use only exact column names from the `profile_data` result. The column names in the examples below (`region`, `revenue`, and so on) are placeholders; always replace them with real columns.

### Step 1: Find the file

* If the user names a file or has one open, use it.
* If no file is named, call `list_directory` on the project's data folder, show the data files found, and ask which one to use. Do not pick one yourself.
* If the named file is not found, suggest the closest matching file names and ask the user to confirm.

### Step 2: Profile it

* Call `profile_data` before anything else (once per file per conversation, unless the file has changed). For Excel files, pass `sheet` if the data is not on the first sheet.
* From the profile, note which columns are categorical, numeric, and date or time, how many distinct values each category has, and any missing values or type problems.
* Pick the main metric (the number the audience cares most about, such as revenue, units, yield, or failures) and the main dimensions (such as region, product, station, or month). If the user did not say and more than one metric is plausible, ask one short question.

### Step 3: Gather the findings

Collect three to five findings, each backed by a tool result. Cover these, in order, where the data allows:

* Headline: the overall total or the main trend (`query_data`, or `analyze_data` with `analysis: trend`).
* Main breakdown: which group leads or lags, and by how much (`analyze_data` with `analysis: compare`).
* Spread or unusual values: outliers or a skewed distribution (`analyze_data` with `analysis: outliers` or `distribution`).
* Relationship: what moves together (`analyze_data` with `analysis: correlations`).
* Data quality, only if it matters: missing values or problems that affect the conclusions (`analyze_data` with `analysis: quality`).

Write each finding as one sentence: the exact number with units, then what it means. For example: "South generated 1,240,500 in revenue, 38% of the total and the highest of any region." Round sensibly and keep the number exactly as the tool returned it, apart from that rounding.

### Step 4: Choose the charts

* If the user did not ask for specific charts, you may leave `charts` out and the tool picks them.
* Otherwise choose four to six charts that tell the same story as the findings, in this order: headline total or trend, main breakdown, spread or outliers, then one relationship.
* Pass one line per chart, in this format:

```
kind | title | x | y | aggregate | series
```

* `kind` is one of: `bar`, `line`, `area`, `pie`, `donut`, `histogram`, `scatter`, `box`, `heatmap`, `stacked`, `grouped`, `treemap`.
* `aggregate` is one of: `sum`, `mean`, `count`, `min`, `max`, `median`.
* Leave a field empty to skip it. Trailing empty fields can be left out.

Write each chart title as the message the slide delivers, not a label. For example, "South earns the most revenue" rather than "Revenue by region". Each message must match a finding you already verified in Step 3.

Match the chart to the story beat:

* Headline trend (x date or time, y numeric): `line | Revenue grew every quarter | date | revenue | sum`
* Main breakdown (x categorical, y numeric): `bar | South earns the most revenue | region | revenue | sum`
* Share of a whole, 6 categories or fewer: `donut | South brings in over a third of revenue | region | revenue | sum`
* Share of a whole, many categories: `treemap | Three products make up most of the revenue | product | revenue | sum`
* Spread and outliers (no aggregate): `box | North has the widest range of order values | region | revenue`
* Shape of one number: `histogram | Most orders are under 500 | revenue`
* Relationship between two numbers (no aggregate): `scatter | Higher prices mean fewer units sold | unit_price | units`
* Two categories at once: `heatmap | Product A sells best in the South | region | revenue | sum | product`
* Parts inside each category: `stacked | Product A leads in every region | region | revenue | sum | product`

Check every chart line against the profile before calling:

* Each column named in the line exists, with that exact spelling.
* The column types suit the chart. Do not sum or average a text column; use `count` instead.
* Pie and donut charts have 6 slices or fewer; stacked, grouped, and heatmap series have about 8 distinct values or fewer.

### Step 5: Build it

* Call `create_presentation` with the data file path, a title, the charts (or none), and the findings.
* Write the presentation title as the overall takeaway or subject, for example "Q3 Sales: South Leads Growth", not just the file name.
* Leave `format` out to get both the HTML deck and the PowerPoint file. Pass `format: pptx` or `format: html` only if the user asks for just one.
* If the call fails, read the error, fix the cause (wrong column name, wrong type, bad line format), and retry once. If it still fails, tell the user what went wrong.
* To change an existing presentation, call `create_presentation` again with the full, updated content.

### Step 6: Reply

1. Give the exact path of each file from the tool result:
   * The HTML deck opens with Preview and prints to PDF with one slide per page.
   * The .pptx opens in PowerPoint, Keynote, or Google Slides with editable charts.
2. List the slides, one line each, in order (slide number and title).
3. Mention any caveat that affects the conclusions, such as missing values in the main metric, excluded rows, or dates that did not parse.

Keep the reply short. Do not repeat every finding in full, paste chart specs, or show raw tool output unless the user asks.
