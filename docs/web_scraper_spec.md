# Web Scraper: Backend Tool Spec

For the backend team. This describes the `#web` application that the `web_scraper` agent (`arch/agents/web_scraper/`) expects, and the `permission.md` fields it must enforce. The agent files assume this behavior; if the implementation differs, update the agent's operation table to match.

## Permission fields (`#web` block in arch/permission.md)

| Field | Values | Enforcement |
|---|---|---|
| Access | enabled or disabled | Anything else counts as disabled. |
| Operations | fetch_page, render_page, extract, screenshot, crawl | Reject any operation not listed. |
| Approval | required or not_required | As for other applications. |
| Allowed Domains | comma-separated hosts, for example `localhost, 127.0.0.1, docs.python.org` | A domain also allows its subdomains. Check every request, every redirect target, and every crawled link. Empty means nothing is allowed. |
| Render Browser | chrome or edge | Browser used by render_page and screenshot (for example Playwright with `channel="chrome"` or `channel="msedge"`). |
| Respect Robots | yes or no | Anything else counts as yes. Check robots.txt before each new domain and path. |
| Max Pages Per Request | number | Hard limit for crawl and for the total pages in one user request. |
| Max Requests Per Minute | number | Per domain. Wait between requests to stay under it. |
| Timeout Seconds | number | Per page load. |
| Max Size MB | number | Stop downloading a response larger than this. |

## Operations

| Operation | Input | Returns |
|---|---|---|
| fetch_page | url | status code, final URL after redirects, content type, HTML (and readable text) |
| render_page | url, optional wait_for (CSS selector) and wait_seconds | the same as fetch_page, after JavaScript runs, in headless Render Browser |
| extract | a page from fetch_page or render_page (or its URL), and mode: `text`, `tables`, `links`, `metadata`, or `selectors` (a map of field name to CSS selector, with an optional item selector for repeated items) | structured data: text, a list of tables (headers and rows), links (text and absolute URL, marked allowed or not), metadata (title, description, dates), or a list of records |
| screenshot | url, optional full_page | the path of a PNG file saved under the workspace |
| crawl | start_url, url_pattern (regex or path prefix), max_pages, and an extract spec | records from every page, plus pages visited, pages failed with errors, and links skipped because their domain is not allowed |

## Safety requirements

1. Network only through `#web`. Block or remove internet access for `#python` and `#terminal`, or the allow-list can be bypassed with a script. `curl` and `wget` are not in Allowed Commands today; keep it that way.
2. Check Allowed Domains on the original URL, after every redirect, and for every crawled link. Block private network ranges (for example `10.x`, `192.168.x`, `169.254.x`) unless their host is listed, so a public page cannot redirect the tool into the internal network.
3. No logins: send no stored cookies, credentials, or browser profile. Use a fresh headless browser context per request.
4. Do not download or run files from pages (installers, scripts, archives). Only HTML and images for screenshots.
5. Return page content to the model as data, clearly marked as untrusted web content.
6. Identify the tool with a clear User-Agent string, and honor 429 `Retry-After`.
7. Log every request (time, URL, status, and the operation) for the user to review.
