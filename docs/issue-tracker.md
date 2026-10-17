# Issue tracker: GitHub

Repository: `awjreynolds/p4ai`. Use `gh` for issue operations.

## Wayfinding operations

The canonical map is the GitHub issue labelled `wayfinder:map`. Decision tickets are native sub-issues, each labelled `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or `wayfinder:task`. Refer to issues by linked title in prose.

Create and edit issue bodies with `--body-file`. Claim a ticket by assigning `awjreynolds` before working it. The assignee is the claim.

Use the native GitHub REST relationships through `gh api`:

- Children: `GET /repos/awjreynolds/p4ai/issues/{map_number}/sub_issues`.
- Attach child: `POST /repos/awjreynolds/p4ai/issues/{map_number}/sub_issues`, with integer `sub_issue_id` (the database ID, not issue number).
- Blockers: `GET /repos/awjreynolds/p4ai/issues/{ticket_number}/dependencies/blocked_by`.
- Add blocker: `POST` to the same endpoint with integer `issue_id` (the blocker's database ID).

The frontier is the map's open, unassigned children whose blockers are all closed. Paginate relationship queries. Preserve native child order when choosing a frontier ticket.

Record an answer as a resolution comment, close the ticket, then append a linked-title gist to the map's Decisions so far. Research assets live on `research/<name>` branches and are linked from resolution comments. The map indexes decisions rather than duplicating their evidence.

Grilling and prototype tickets require the human's participation; do not resolve them by inventing answers. Charting may resolve research tickets but does not resolve the human decision tickets.
