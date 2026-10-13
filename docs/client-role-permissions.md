# Client roles and menu permissions

Role profiles remain shared across projects. Project assignment is stored on the
user. The role editor explains whether the role type has organization, project,
team, or individual scope.

Client users must have an assigned project and no reporting parent. Super Admin
and Admin may create/manage them. Clients can only read allotted menus. Both the
session permission response and backend authorization reject Client write access,
even if an invalid legacy feature assignment contains a write grant. Password and
session management remain available for the client's own account.

Client dashboard, report, chart hold, project member, login hour, user directory, and
project reads are restricted to the assigned project. Role profiles are shared,
so an explicitly allotted Roles & Features menu reads shared role configuration.
The client API permits only the implemented read endpoints and normal account
operations; other endpoints are denied.

Menu features now match navigation labels: Dashboard, Reports, Chart holds, Team,
Login Hours, Projects, Users, Roles & Features, and Help & guides. Team
controls team administration independently of Users. Team includes stages. Coders see the same feature as Stages and can only read
their own stage history. Coverage requires Reports alongside Team. Help & guides
and Chart holds are read-only features in the role editor. Legacy stage_view
grants are presented as Team read access without granting team administration.
The feature catalog and role responses expose a single Team entry.

Apply migration `fa3d6c01490c` before deploying the frontend. It adds the Client
role type and the Team, Chart holds, and Help & guides features, renames existing
feature titles, and copies compatible permissions to preserve existing menu
access. It creates no Client accounts and grants no access to a client by default.
No new database tables, browser keys, or public Supabase policies are introduced.

Validated against a separate local PostgreSQL database. Deploy the matching
backend and frontend together; do not run the new frontend against the old
feature seed data.


Client Coding performance uses `/dashboards/client`: QA (2LR) and coders (1LR)
are separate scopes with calendar goals, production, efficiency, CPD, daily tables,
and source history. A selected person is checked against the assigned project.
Response schemas omit training stages, login/attendance fields, reporting lines,
employee identifiers, and reviewer metadata. Existing client report/dashboard responses
also omit stage and login fields. `/team/client-members` exposes only names and
review levels; client access to stage endpoints is denied. Sidebar allotment
continues to use role features. No new database migration is needed.


The client Overview combines the already scoped QA and coder chart counts and
calendar goals. Efficiency and CPD are recomputed from combined totals and
capacity, rather than averaging percentages. QA/Coder table switches keep the
Overview consolidated; dedicated QA and Coder views show their own summaries.
No team selector or reporting hierarchy is exposed.
