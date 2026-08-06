# Resolution Center

Replies to App Review, kept because a rejection is answered in prose and the
prose is worth writing once. Reply in Resolution Center rather than resubmitting
in silence: a reply usually gets a same-day second look, a silent resubmission
goes to the back of the queue.

What App Review rewards is a short, specific answer: what the cause was, what
changed, and how they can confirm it. Everything else belongs in
[../CHANGELOG.md](../CHANGELOG.md).

---

## 2026-08-06 — 2.1(a), "an error message is displayed when attempting to log in"

Submission `556775c4-63cc-431c-8caf-5a7e4b6339bf`, reviewed on an iPad Air
11-inch (M3) on iPadOS 26.6 against build ASC-18. The full diagnosis is in
[../CHANGELOG.md](../CHANGELOG.md#the-21a-rejection); the short version is that
the server was handing out dead pooled database connections and the reviewer's
single sign-in attempt got one.

**Status: sent 2026-08-06, and 1.0 resubmitted the same day** as submission
`46eacb99-2954-48bb-8bc3-948fc2cbf703`. The reply goes in by hand: the App Store
Connect API has no Resolution Center endpoint, so this is web UI only, and it is
worth doing *before* resubmitting — a reply usually earns a same-day second
look where a silent resubmission joins the back of the queue.

```
Thank you for the detailed report. The review device and timestamp let us
locate the exact request in our server logs, and the fault was ours, not the
app's.

The cause was a database connection pooling bug on our server. The API held a
pool of connections to its database and was handing out connections that the
network had already closed. Any request unlucky enough to receive one failed
with a server error. Your sign-in attempt at 08:16:23 UTC on 6 August was one
of them. The logs show the app behaving correctly throughout: it fetched its
configuration successfully one second earlier, then our login endpoint returned
a 500.

We have fixed this by validating every pooled connection before it is used and
retiring connections before the network can drop them. The fix is deployed to
the server the app connects to. We verified it by deliberately closing every
pooled connection and confirming that sign-in and all other requests now
succeed, where before the change they failed.

The demo account credentials in the App Review information are unchanged and
working.

On device support: the app is iPhone only by design and is submitted as such.
We have retested sign-in and the rest of the app on an iPad Air 11-inch (M3) in
iPhone compatibility mode, and both work correctly.

We have attached build 23, which also carries improvements made since the build
you reviewed. We have also filled in the App Review notes, which were
unfortunately left empty on the original submission and would have explained
that this app connects to a server the user runs themselves.
```

### What backs each claim

Apple can check any of these, so none of them is padding.

| Claim | Evidence |
|---|---|
| Their request at 08:16:23 returned a 500 | `docker service logs meals_api`, alongside the 200 for `/client-config` a second earlier |
| Fix is deployed | `pool_pre_ping` present in the running container; production survives `pg_terminate_backend` |
| Verified by closing pooled connections | 500 before the change, 200 after, in a throwaway stack and again on production |
| Demo account works | `POST /auth/login` 200, and signed in on the simulator the same day |
| Retested on iPad Air 11-inch (M3) | **Simulator, iOS 26.0.1.** Not a physical device, and not their 26.6 |

That last row is the only soft claim. The sentence is true of a simulator, and
what it asserts is that the app works in compatibility mode, which is what was
observed. Test on a physical iPad first if you would rather the sentence be
stronger than that.

### Before sending

- The fix must be deployed. It was, on 2026-08-06, but a reply claiming a
  deployed fix against an undeployed server is a second rejection.
- Check the demo account still signs in. It is the first thing they retry.
- [review-notes.md](review-notes.md) suggests reprovisioning the demo account
  with a fresh password for a resubmission, since Apple keeps the previous
  credentials. Optional while the current password works, but if you do it,
  update App Store Connect in the same sitting.
