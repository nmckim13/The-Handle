# Racer survey: rollout and operations

On October 30, 2026, the owner explicitly authorized applying the survey
migrations and deploying the survey/results dashboard. Migrations 019 and 020
were installed in The Handle's production Supabase project through the tracked
migration API. Hosted REST checks confirmed public summaries work, individual
table reads are denied, and invalid submissions are rejected. Existing table
permissions and policies were unchanged. No test responses were retained.

The matching frontend is released through the existing Git-linked Vercel
deployment. Verify **`https://yardkartz.com/#survey`** and **Admin → Survey
Results** after deployment. The steps below also document installation in a
fresh environment; **do not reapply these once-only migrations to production**.
This document does not authorize additional database or deployment changes.

## Rollout and verification

1. Run the disposable local PostgreSQL checks from the repository root:

   ```sh
   PATH=/usr/lib/postgresql/15/bin:$PATH PYTHONDONTWRITEBYTECODE=1 python3 migrations/tests/test_racer_survey.py
   ```

   Requires PostgreSQL server/client binaries and an unprivileged OS user. The
   suite creates Unix-socket-only clusters, applies fixtures before and after
   migration 010, and removes its clusters on exit. It never accepts a remote
   database URL. It checks every enum, invalid shapes/types, concurrent retries,
   conflicting payloads, server-owned dates, privacy, and unchanged existing
   grants/policies.
2. Obtain explicit operator approval for the target database and a normal backup
   before applying `migrations/019_racer_survey.sql`, then
   `migrations/020_racer_survey_summary.sql`, each once, as their trusted
   SQL owner (normally `postgres`). Use the approved database SQL console or
   operator-controlled migration workflow. Do not apply unrelated pending
   migrations just to enable this survey. Migration 019 is self-contained and
   works with the site's pre-010 public-admin configuration or after 010; it has
   no `is_admin()` dependency and changes no existing schema/default grants.
3. Deploy the matching `index.html`, `admin/index.html`, and `racer-survey.js` frontend through the
   separately approved frontend release. Check `/#survey`, six required
   single-choice questions, success, failed requests, and a same-request retry.
   Keep public copy neutral: ask about current experience, not proposed rules
   or equipment brands. Preparation-time labels must not overlap: **less than
   1 hour**, **1 to less than 3 hours**, **3 to less than 6 hours**, **6 or more
   hours**, and **not applicable**.
4. Open **Admin → Survey Results** (also in Overview's Quick Actions).
   Verify empty, populated, Refresh and unavailable states. This page is
   intentionally public (`admin-auth.js`); the owner approved public aggregate
   summaries. `racer_survey_summary()` returns only a total and per-question
   answer counts. The browser shows all options, including zero counts, and
   percentages rounded to one decimal using all submissions as the denominator.
   It accepts no date, person, subgroup or other filters and exposes no individual
   rows. Verify clients still cannot read or directly mutate
   `racer_survey_responses`, including `service_role`.

## Submission contract and limits

Only `public.submit_racer_survey(p_answers jsonb, p_request_id uuid)` accepts
submissions. It returns the supplied per-submission UUID. The frontend must
generate a random request UUID unrelated to any person, keep the same UUID and
unchanged answers for a retry after an uncertain network result, and never use
a driver/user identifier as the request UUID. Identical JSON objects (regardless
of key order) replay safely; changing answers under the same UUID fails with
SQLSTATE `22023` without changing the original response.

The exact six keys and case-sensitive string values are:

| Key | Allowed values |
| --- | --- |
| `attendance` | `regularly`, `occasionally`, `not_recently`, `never` |
| `enjoyment` | `racing`, `people`, `improving`, `watching`, `not_yet` |
| `barrier` | `schedule`, `prep_time`, `cost`, `mechanical`, `no_kart`, `nothing`, `other` |
| `prep_time` | `under_one`, `one_to_three`, `three_to_six`, `six_plus`, `not_applicable` |
| `prep_task` | `tires`, `engine`, `clutch_chain`, `cleaning`, `other`, `not_applicable` |
| `improvement` | `less_prep`, `more_track_time`, `lower_cost`, `clearer_rules`, `schedule`, `nothing`, `other` |

There is no free-text field, including for `other`. Missing/extra keys, SQL/JSON
nulls, nonstring choices, unknown values and a null request ID are rejected with
SQLSTATE `22023`. Use normal READ COMMITTED requests; the unique ID and separate
post-insert SELECT serialize concurrent retries without updating existing rows.

The only stored fields are the random request `id`, six-choice `answers`, and
server-default `submitted_on` date (`current_date`, in the database session's
time zone). No name, driver ID, email, IP address, user agent, precise timestamp,
or authentication identity is collected by this RPC. It does not inspect JWT
claims. Table privileges are revoked from `PUBLIC`, `anon`, `authenticated`, and
`service_role`, with RLS enabled and no client policies. Only the trusted SQL
owner (and inherent database superuser authority) can read responses. Only
`anon` and `authenticated` receive submission EXECUTE; not `PUBLIC` or
`service_role`. Keep SQL-owner credentials out of browsers and application code.

**Anonymous does not mean verified one-person-one-vote.** A browser completion
flag discourages repeat submissions only; another browser, cleared storage, or
direct RPC calls can submit again using a new UUID. Do not claim identity
verification or use these responses as a binding vote. Hosting, proxy, network,
and database infrastructure may retain request logs independently of this
minimal table; do not promise that no request metadata exists anywhere.

**Public aggregate privacy limits:** these are overall marginal counts, not
individual records or cross-question combinations. Small totals (especially
one response), prior knowledge, and observing count changes can still allow
inferences about someone's choices. Do not promise that public counts make
such inference impossible. Public survey copy discloses overall counts are public.

## Trusted read-only reporting (SQL owner only)

Run the following manually in a trusted SQL session, **not the public admin UI
or browser REST client**. No new roles, grants, views, functions, or policies are
needed. This transaction returns a total count and counts/percentages for every
question/answer, including zero-count options. Percentages use all submissions
as the denominator (every submission answers all six questions); an empty
survey reports zero percent. Rounding can make displayed sums differ from 100.
Do not publish small-group slices or raw response rows.

```sql
begin transaction isolation level repeatable read read only;

select count(*) as total_responses
from public.racer_survey_responses;

with choices(question, options) as (
  values
    ('attendance', '["regularly","occasionally","not_recently","never"]'::jsonb),
    ('enjoyment', '["racing","people","improving","watching","not_yet"]'::jsonb),
    ('barrier', '["schedule","prep_time","cost","mechanical","no_kart","nothing","other"]'::jsonb),
    ('prep_time', '["under_one","one_to_three","three_to_six","six_plus","not_applicable"]'::jsonb),
    ('prep_task', '["tires","engine","clutch_chain","cleaning","other","not_applicable"]'::jsonb),
    ('improvement', '["less_prep","more_track_time","lower_cost","clearer_rules","schedule","nothing","other"]'::jsonb)
), total as (
  select count(*) as total_responses from public.racer_survey_responses
), counts as (
  select a.key as question, a.value as answer, count(*) as response_count
  from public.racer_survey_responses r
  cross join lateral jsonb_each_text(r.answers) a
  group by a.key, a.value
)
select c.question, option.answer,
       coalesce(n.response_count, 0) as response_count,
       coalesce(round(100.0 * coalesce(n.response_count, 0)
                      / nullif(t.total_responses, 0), 1), 0) as percent,
       t.total_responses
from choices c
cross join lateral jsonb_array_elements_text(c.options) as option(answer)
cross join total t
left join counts n on n.question = c.question and n.answer = option.answer
order by c.question, option.answer;

commit;
```

There is deliberately no client-accessible individual read-back or deletion RPC.
Migration 020 grants only summary EXECUTE to `anon` and `authenticated`, not
`PUBLIC` or `service_role`; it changes no table privileges. Pause
collection by removing/disablement of the frontend entry point and, if required,
an explicitly approved operator revocation of submission EXECUTE. Removing the
frontend alone does not disable direct RPC submissions. Retention, exports, or
deletion require a separate trusted operator decision; do not drop the table as
an automatic rollback or change other race-night grants.
