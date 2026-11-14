# Loom demo script (2-3 minutes)

A walkthrough of one real lead in the GHL sandbox, from form to booked meeting, ending on the dashboard.

## Before recording

- The Render service is awake: open `https://<app>.onrender.com/health` a minute before (the free tier sleeps after 15 minutes).
- `DASHBOARD_TOKEN`, OpenAI key, calendar, pipeline and Booked stage are set (README Steps 3, 4, 6). The `Intro call` calendar has free times this week.
- Run a few test leads first so the dashboard has numbers; the recorded lead adds one more.
- Tabs open, left to right: GHL **Contacts**, the contact's **Conversations**, **Calendars**, **Opportunities → Speed to Lead**, Render **Logs**, `/dashboard?token=...`, the GitHub README. Phone mirrored on screen (or held to the camera).
- Hide the token: crop the address bar or zoom the page when the dashboard is shown.
- Say only what the screen shows. Read the numbers off the dashboard; do not quote numbers from memory.

## Script

| Time | Scene | Show | Say |
|------|-------|------|-----|
| 0:00-0:15 | Hook | GitHub README, top: pitch line and architecture diagram | "Leads go cold in minutes. This is the Speed-to-Lead module of AI GTM Engine, my AI go-to-market engine on GoHighLevel: a new lead gets a text within seconds, gets qualified by SMS and books a meeting, with no one touching it. Let me show you a real run in my sandbox." |
| 0:15-0:30 | How it fits | Architecture diagram, point at GHL, the app, the LLM | "GoHighLevel keeps the contacts, messages, calendar and pipeline. My FastAPI app listens to GHL webhooks, decides what to do, and calls the GHL API. The LLM only writes the words and reads the answers; the code owns every decision." |
| 0:30-0:50 | New lead | GHL **Contacts → Add Contact**: first name, your mobile number, Save. Then the phone. | "I add a lead the way a form would. ... And here's the text on my phone, a few seconds later, already asking what they need." |
| 0:50-1:25 | Qualify | Reply from the phone to the three questions (need, budget, timing). Keep the contact's **Conversations** tab visible next to it. | "I answer like a lead would. It asks one thing at a time: what they need, budget, timing. Every message is in GHL's own conversation history, so the team sees everything." |
| 1:25-1:50 | Book | The SMS with 3 numbered times; reply in plain words, e.g. "the second one". Then the confirmation SMS. | "Once it has all three answers it offers real free times from my GHL calendar. I just say 'the second one', and it books it and confirms." |
| 1:50-2:10 | GHL updated | **Calendars** view with `Intro call - <name>`; **Opportunities** with the card in **Booked**; the `booked` tag on the contact | "In GHL: the appointment is on the calendar, the opportunity moved to Booked, and the contact is tagged. If a lead asks for a person or isn't a fit, it tags them handoff or not-a-fit and stops texting." |
| 2:10-2:20 | Under the hood (optional, cut if long) | Render **Logs**: `NEW -> QUALIFYING first_reply_seconds=...` then `-> DONE` | "The log shows each state change, and how many seconds the first text took." |
| 2:20-2:45 | Numbers | `/dashboard`: the five cards, then the lead just recorded at the top of the table with state `DONE` | "And the dashboard: <N> leads in my sandbox, median first reply <X> seconds, p90 <Y> seconds, <Q>% qualified, <B>% booked. The lead we just ran is right here at the top, done." |
| 2:45-2:55 | Close | Back to the dashboard cards | "Signed webhooks, OAuth install for client accounts, and tests for every API call. If you run GoHighLevel and want leads answered in seconds, let's talk." |

Fill `<N>`, `<X>`, `<Y>`, `<Q>`, `<B>` from the dashboard on the day of recording, and use the same numbers in the README "Results" section and the Upwork portfolio item.
