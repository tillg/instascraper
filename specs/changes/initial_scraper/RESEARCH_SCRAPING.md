Together wit a fellow agent I went thru a research process on how to build such a tool. Here is his finding:

# Step 1: Research

Bottom line

For your exact goal, the safest and most maintainable route is not a scraper that logs in with your Instagram username/password. It is an official Meta/Instagram API integration using OAuth, where the Instagram account owner grants your app permissions. The Meta docs show that Instagram media objects expose fields such as caption, media_type, media_url, thumbnail_url, permalink, shortcode, timestamp, username, comments_count, and a comments edge, but the same docs also state that the media API only returns data for media owned by an Instagram professional/business account and cannot be used to fetch media owned by personal accounts.  

That means:

If the URL is for your own or an authorized professional Instagram account: yes, build it with the Instagram Platform / Graph API.

If the URL is any arbitrary public Reel/post on Instagram: there is no reliable, sanctioned API path that gives you the raw video/image plus caption plus “top 10 comments.” oEmbed can help display public posts, but it is for embed HTML/basic metadata, not a full media-and-comments export.  

How the program logs in: it should use OAuth / Business Login for Instagram, not raw credentials. Meta describes Business Login as a login flow that asks for permissions to access the app user’s Instagram professional account data; access tokens from that flow are short-lived by default, while longer-lived tokens are time-limited and must be managed securely.  

A compliant output pipeline would look like this:

instagram_export/
  DXOCAyzEX8i/
    post.md
    metadata.json
    media/
      reel.mp4
      thumbnail.jpg

The backend flow would be:

1. App sends Instagram URL to backend.
2. Backend extracts shortcode: DXOCAyzEX8i.
3. Backend uses OAuth token for the authorized Instagram account.
4. Backend resolves the shortcode/permalink to an IG_MEDIA_ID
   by listing/maintaining authorized media, not by scraping the web UI.
5. Backend calls IG Media endpoint for caption/media_url/thumbnail/comments.
6. Backend downloads media_url immediately and writes post.md + media files.
7. Backend records provenance: API version, token account, timestamp, fields, sort method.

For comments, you can fetch comments through the media comments edge, and the docs list comment-related permissions such as instagram_business_manage_comments / instagram_manage_comments.   But “top 10 comments” is a problem: Instagram’s UI ranking is algorithmic/personalized, and the official API does not simply give you “the same top 10 comments a user sees.” You would need to define your own rule, for example “top 10 by like_count among the comments returned by the API,” and document that choice.

Do not build this by automating a normal Instagram login with Selenium/Playwright, session cookies, rotating proxies, or password submission. Meta says using automation to collect data from its platforms without permission violates its Terms of Service, and it describes active anti-scraping enforcement including rate limits, data limits, account disabling, and deletion requests.  

⸻

1. The practitioner

Core position: Build this as an authorized export tool, not a general Instagram scraper. In practice, the hard parts are not downloading files; they are OAuth, App Review, permissions, token refresh, API version changes, missing fields, rate limits, and deciding what “top comments” means.

Strongest evidence: The IG Media endpoint exposes exactly the fields you need for a legitimate export—caption, media type, media_url, thumbnail_url, permalink, shortcode, timestamp, username, and comments—but it also has important limitations: it only returns media data for Instagram professional accounts, media_url can be omitted for copyright/flagged media, and thumbnail_url only exists for video media.   Meta also requires App Review before apps can request advanced permissions from general users.  

What they would tell you that no one else would: Maintain your own mapping of permalink/shortcode -> IG_MEDIA_ID as soon as content enters your system. Trying to reverse arbitrary Instagram URLs after the fact is where most fragile implementations start.

⸻

2. The academic

Core position: The data you collect is not “the Instagram post”; it is a platform-mediated view of the post. API data, browser-visible data, and logged-in user-visible data can differ, so a serious research dataset needs provenance, collection time, account context, and ranking/sampling rules.

Strongest evidence: Peer-reviewed work in Nature Human Behaviour argues that platform-controlled APIs restrict reproducibility and transparency in social media research.   A 2024 research framework from NYU’s Center for Social Media and Politics likewise argues that web scraping research must handle legal, ethical, institutional, and scientific issues explicitly, especially as official data access becomes more restricted.  

What they would tell you that no one else would: Put a “methods header” in every post.md: API version, collection timestamp, account used, endpoint fields, comment pagination depth, and whether “top comments” means UI ranking, most recent, or highest like_count.

⸻

3. The skeptic

Core position: The mainstream “just use the official API” answer is convenient for Meta, but it means Meta controls what outsiders can measure. Still, the skeptic would draw a bright line between public logged-out web data and logged-in, credentialed, anti-bot scraping; the latter is far riskier technically, contractually, and privacy-wise.

Strongest evidence: In the U.S., cases like hiQ v. LinkedIn and Van Buren narrowed some Computer Fraud and Abuse Act theories for public, non-login-gated scraping, but legal analyses emphasize that this does not eliminate copyright, contract, privacy, trespass, or other claims.   For Instagram specifically, Meta’s own anti-scraping position is explicit: unauthorized automated collection violates its Terms.  

What they would tell you that no one else would: If your research question depends on “what a normal user sees,” the official API may be the wrong instrument. But proving that may require a formal audit design, not a production scraper hidden behind credentials.

⸻

4. The economist

Core position: The API is not mainly expensive because Meta charges per call; it is expensive because access is controlled through permissions, review, compliance, and maintenance. Money flows to Meta through platform control and advertising, and to vendors/consultants who solve OAuth, App Review, cross-platform posting, analytics, and compliance complexity.

Strongest evidence: Meta’s 2018 post-Cambridge Analytica changes tightened access to Events, Groups, Pages, Facebook Login, and Instagram Platform APIs, requiring more approval and reducing broad data access.   Researchers have also argued that platform-owned APIs hinder transparency and scientific knowledge because platforms control access terms.  

What they would tell you that no one else would: Budget this as an ongoing integration product, not a one-off scraper. The durable cost is token lifecycle, reauthorization, app review changes, API version migration, support tickets, and compliance documentation.

⸻

5. The historian

Core position: This pattern has happened before: open-ish platform APIs enable an ecosystem, abuse or scandal triggers backlash, the platform locks down access, and developers rebuild through approved channels. Instagram’s current API constraints are best understood as part of the post-2018 platform governance shift, not as a random technical inconvenience.

Strongest evidence: In April 2018, Meta/Facebook announced major restrictions after Cambridge Analytica, including accelerated Instagram Platform API deprecation and stricter review.   Reporting at the time described sudden Instagram API rate-limit reductions from 5,000 calls/hour to 200 calls/hour and cutoffs for some developers.  

What they would tell you that no one else would: Design an adapter layer so your export logic is not tightly coupled to Instagram’s current fields, endpoints, or permission names. Platform APIs change when the political, regulatory, or business climate changes.

⸻

Recommended path for your project

Build an “Instagram Authorized Exporter”:

1. Use OAuth / Business Login, not Instagram credentials.
2. Support only media from accounts that authorized your app, or clearly label other modes as unsupported.
3. Request only the permissions you need: basic media read plus comment management/read permissions where applicable.
4. Resolve URLs to media IDs from authorized media lists or your own stored mappings.
5. Fetch media metadata and comments through official endpoints.
6. Download media_url/thumbnail_url immediately when returned.
7. Write post.md, metadata.json, and media files.
8. Define “top 10 comments” as your own reproducible rule, ideally “top by like_count among fetched comments.”
9. Add GDPR/privacy review if you store usernames, comment text, timestamps, or profile-linked data in Europe; public social media data can still be personal data, and European data-protection analysis treats scraping/collection of personal data as regulated processing.  

A good post.md format:

# Instagram Reel DXOCAyzEX8i
Source URL: https://www.instagram.com/reel/DXOCAyzEX8i/
Fetched at: 2026-06-26T...
API version: v...
Authorized account: @...
Media type: VIDEO
Permalink: ...
Caption:
...
## Media files
- media/reel.mp4
- media/thumbnail.jpg
## Comments
Selection rule: top 10 by like_count among fetched API comments.
| Rank | Username | Likes | Timestamp | Text |
|---:|---|---:|---|---|
| 1 | @example | 123 | ... | ... |

The main architectural decision is simple: OAuth/API export for authorized content is viable; password-based scraping of arbitrary Instagram URLs is not a stable or compliant foundation.

# Step 2: Map contradictions

1. Direct contradictions between perspectives

Conflict A — “Use the official API” vs “the official API controls what can be known”

The Practitioner says the right implementation is an OAuth/API exporter: authorize the Instagram account, fetch media/comment data through Meta endpoints, and avoid password-based scraping. That claim is supported by Meta’s current Instagram Platform model, where Business Login lets an app ask permission to access an Instagram professional account’s data, and access tokens follow OAuth 2.0.  

The Academic and Skeptic clash with that: they argue that official APIs are not neutral windows into Instagram but platform-controlled access channels. The academic evidence is strong here: Nature Human Behaviour argues that platform-owned APIs hinder access, transparency, reproducibility, and scientific knowledge.  

The clash:
The Practitioner treats the API as the safest source of truth for building the tool. The Academic/Skeptic treat the API as a filtered, incomplete source that may not answer the real research question.

⸻

Conflict B — “Scraping violates platform rules” vs “some public scraping has won in court”

The Practitioner, Economist, and Historian lean toward: do not build this around scraping or user credentials because Meta/Instagram rules prohibit automated access/collection without permission. Instagram’s terms say users may not create accounts or access/collect information in an automated way without express permission, and Meta’s automated data collection terms require express written permission unless explicitly authorized.  

The Skeptic pushes back: U.S. case law has sometimes protected or at least tolerated scraping of public, logged-out web data. In hiQ v. LinkedIn, the Ninth Circuit said hiQ had raised serious questions about whether LinkedIn could use the CFAA to block access to public profile data, and in Meta v. Bright Data, Judge Chen ruled that Meta’s terms could not be construed to prohibit Bright Data’s logged-off scraping of publicly available Facebook/Instagram data.  

The clash:
The anti-scraping side says “Meta prohibits this.” The Skeptic says “that does not automatically mean all public-data scraping is legally barred.” But the Skeptic’s evidence is much weaker for your exact scenario because you mentioned providing Instagram credentials, which moves the design toward logged-in, account-based automation, not logged-out public scraping.

⸻

Conflict C — “A URL is enough” vs “an authorized account/media object is the real unit of access”

Your product idea starts with a URL such as an Instagram Reel link. The Practitioner would say the URL is only an input hint; the real object your backend needs is an authorized Instagram media object reachable through Meta’s API.

The official API model supports that position: Instagram media docs expose fields like permanent URL, shortcode, thumbnail URL, comments_count, and like_count; comment docs expose comment fields such as comment ID and like_count; and comments are available through the media comments edge.  

The Skeptic and Academic would object that this model fails for arbitrary public URLs, which are often exactly what researchers, journalists, or analysts want to study. The web-scraping research literature notes that as platforms restrict official channels, researchers increasingly face pressure to scrape web data, but doing so introduces legal, ethical, institutional, and scientific problems.  

The clash:
The Practitioner optimizes for a compliant product. The Academic/Skeptic optimize for observing the public platform as it is experienced.

⸻

Conflict D — “Top 10 comments” as an API rule vs “top comments” as a UI/algorithmic phenomenon

The Practitioner would define “top 10 comments” operationally: for example, fetch comments through the API and sort by like_count, then save the top 10. That is reproducible because Meta documents comment fields such as like_count.  

The Academic would say this is not the same as “the top 10 comments on Instagram” unless Instagram itself exposes the same ranking rule. A reproducible API-derived top-10 list may be useful, but it is a constructed measurement, not necessarily the same as the ranked comment view a user sees in the app.

The clash:
The Practitioner wants a deterministic export. The Academic wants measurement validity: “top” must be defined, not assumed.

⸻

Conflict E — “Build for current docs” vs “the platform can change the ground under you”

The Practitioner uses current documentation as the engineering contract. The Historian says that contract is temporary because platform access has changed sharply before.

The historical evidence supports the Historian: in April 2018, Facebook announced restrictions after the Cambridge Analytica scandal and made the deprecation of the Instagram Platform API effective immediately; reporting at the time described developers being cut off or throttled, including reductions from 5,000 to 200 calls per user per hour.  

The clash:
The Practitioner says “follow the docs.” The Historian says “yes, but build as if the docs may change.”

⸻

2. Strongest and weakest evidence

Strongest evidence: the Practitioner.
For your actual implementation question, the Practitioner has the strongest evidence because Meta’s own current developer docs and terms directly control what an Instagram integration can officially do: OAuth-based access, professional-account authorization, media fields, comment endpoints, App Review, and limits on automated collection.  

Second strongest: the Historian.
The Historian has strong documentary evidence that platform access really does change after scandals, regulation, or business pressure. The 2018 API restrictions are not theoretical; Meta announced them, and developers experienced sudden access/rate-limit changes.  

Strong but narrower: the Skeptic.
The Skeptic has strong evidence only for a narrow claim: some public, logged-out scraping has survived legal challenge. But that evidence does not cleanly authorize logged-in Instagram automation using user credentials, and it does not remove privacy, contract, copyright, or jurisdictional risk.  

Weakest evidence: the Economist.
The Economist is directionally plausible but has the least direct proof. It is easy to show that Meta benefits from controlling access and that vendors profit from API complexity, but it is harder to prove that those financial incentives are the primary cause of each API restriction rather than privacy, security, regulatory pressure, spam prevention, or reputational risk.

⸻

3. The one question that would resolve the biggest contradiction

Must your tool work for arbitrary Instagram URLs, or only for media from Instagram accounts that have explicitly authorized your app via OAuth?

If the answer is authorized accounts only, the contradiction mostly resolves in favor of the Practitioner: build an OAuth/API exporter.

If the answer is arbitrary public posts/reels, the contradiction shifts toward the Academic/Skeptic: the official API likely cannot fully satisfy the research goal, and the project becomes a legal/ethical/research-methods problem rather than just an engineering problem.

⸻

4. What every perspective agrees on

Every perspective agrees on this:

Getting “URL → media file(s) + caption + comments” is not just a simple download problem. It is an access-governance problem.

They all agree, even if for different reasons, that Instagram data access is constrained by account authorization, platform rules, ranking ambiguity, API limitations, legal risk, and platform change. Meta’s own docs show that legitimate access is permission/token-based, while its terms restrict unauthorized automated collection; academic and legal literature separately confirm that platform data access is contested and methodologically fragile.  

The likely true statement is:

A robust version of this product needs explicit authorization, provenance logging, a reproducible comment-selection rule, and a plan for legal/API changes.

⸻

5. The blind spot none of the perspectives fully addressed

The biggest blind spot is downstream rights and lifecycle management of the exported directory.

The five perspectives focused on how to access Instagram data. They did not fully address what happens after you save the Reel, image, caption, usernames, and comments into a local folder.

That matters because the exported directory may contain:

* copyrighted photos/videos,
* personal data from commenters,
* usernames and timestamps,
* deleted or edited comments that may later disappear from Instagram,
* media that the creator did not license you to reuse outside Instagram.

Instagram’s own terms say it does not claim ownership of user content; users grant Instagram a license, but that does not automatically grant your app broad reuse rights.   Instagram’s IP guidance also says its terms do not allow posting content that violates others’ copyright or trademark rights.  

For European use, this blind spot is even more important. CNIL’s 2026 web-scraping guidance says collection of online personal data through scraping generally needs safeguards for data-subject rights, and the EDPB emphasizes that processing personal data requires a valid legal basis, fairness, purpose limitation, and attention to rights such as objection.  

So the overlooked research question is:

Once the data is exported, what are you allowed to keep, for how long, who can access it, how do you handle deletion/objection requests, and what reuse rights do you actually have for the media and comments?

That may be the most valuable finding because it changes the product spec. The exporter should not merely create:

post.md
reel.mp4
thumbnail.jpg
comments.json

It should also create something like:

rights_and_provenance.json
retention_policy.md
deletion_log.json
source_api_version.txt

In other words: the missing layer is not scraping. It is data governance for the exported artifact.

# Step 3: Synthetize

1. THE ONE-PARAGRAPH SUMMARY

An Instagram “URL → folder with media, caption, and top comments” tool sounds like a scraper, but the real issue is platform-governed data access: the compliant path is an OAuth-based Instagram Platform integration for Business/Creator accounts that have authorized your app, while arbitrary public Reel/post extraction with login credentials is fragile, legally exposed, and methodologically ambiguous. Meta’s current model centers on Business Login/OAuth permissions, App Review, and API objects such as IG Media and IG Comments; Meta’s terms also restrict automated collection without permission.   The deeper nuance is that the official API is both the safest engineering path and a controlled measurement channel: academics warn that platform-owned APIs can hinder transparency and reproducibility, while legal developments around logged-out public scraping show that “scraping is always illegal” is too simplistic but does not make credential-based Instagram automation safe.  

⸻

2. THE 5 KEY FINDINGS, RANKED BY RELIABILITY

Rank	Finding	Why it is reliable	Supported by	Challenged by
1	Do not design around Instagram username/password login; design around OAuth authorization.	Meta’s Instagram Platform documentation describes Business Login as a permission flow for accessing an app user’s Instagram professional account data, and Meta’s overview states access tokens follow OAuth 2.0 and are required for most API calls.  	Practitioner, Economist, Historian	Skeptic, but only if the target is logged-out public scraping rather than account-based automation
2	Your product scope must be “authorized account export,” not “any Instagram URL export,” unless you intentionally enter a research/legal gray zone.	Meta’s platform is structured around authorized API access, permissions, and App Review; Meta’s IG Media and Comments references expose useful fields/edges, but through the platform’s object/permission model, not as a universal public URL extraction API.  	Practitioner, Historian, Economist	Academic and Skeptic, because authorized API access may not represent what arbitrary users see on Instagram
3	“Top 10 comments” is not a neutral requirement; it is a measurement choice.	The API can expose comment data and media comment counts, but “top” in the Instagram UI may be algorithmic, personalized, or context-dependent, so a reproducible export should define its own rule, such as “top 10 by like_count among fetched comments.”  	Academic, Practitioner	Product intuition/user expectation, because users may assume “top” means the same ranking shown in the app
4	Logged-out public scraping has some legal support, but that does not rescue logged-in Instagram scraping with credentials.	The Bright Data ruling was narrow: it concerned logged-off scraping of publicly available Facebook/Instagram data and did not decide whether scraping behind a login screen violates Meta’s terms; it also did not make public scraping universally legal.  	Skeptic	Practitioner, Economist, Historian
5	The exported folder creates a second product problem: rights, retention, and privacy governance.	CNIL’s 2026 guidance says collection of online personal data by scraping must include safeguards for data-subject rights, and it flags risks involving privacy, intellectual property, reuse, sensitive data, and erasure.  	Academic, Economist, Historian	Practitioner, if implementation is treated as merely “download files and write markdown”

The most reliable finding is OAuth/API-first design because it is grounded in current platform documentation and enforceable product constraints. The least reliable—but still useful—finding is the Economist’s incentive analysis: it explains why platform access is shaped the way it is, but proving intent behind every API restriction is harder than proving the restrictions exist.

⸻

3. THE HIDDEN CONNECTION

The non-obvious link is this: the same thing that makes the official API safer also makes it incomplete as a research instrument. OAuth, App Review, permissions, and professional-account boundaries are what make a compliant product possible; but those same boundaries mean the data is not an unbiased snapshot of “Instagram as seen by the public.”  

That creates the central strategic fork:

If the goal is a product:
    Build an authorized exporter.
If the goal is independent observation of public Instagram:
    Treat it as a research/audit project with legal, ethical, and methodological review.

The hidden connection is that access method determines truth claims. A folder created through the API can truthfully say “this is the authorized API view of this media at this time.” It should not claim “this is exactly what any Instagram user saw in the app.”

⸻

4. THE ACTIONABLE INSIGHT

For someone in your role—the person deciding how to build or research this exporter—do not start by solving scraping mechanics. Start by deciding the product boundary, then make the architecture enforce that boundary.

Build it as an Authorized Instagram Exporter:

1. Remove username/password login from the design. Use Instagram Business Login/OAuth and store tokens securely.  
2. Only support content from accounts that authorized your app. Make arbitrary public Reel/post URLs explicitly unsupported unless you create a separate legal/research workflow.
3. Convert the URL into an internal media lookup only after authorization. Treat the shortcode/permalink as a hint, not as proof that the backend may fetch the content.
4. Define “top 10 comments” in your own reproducible way. For example: “top 10 by like_count among comments returned by the API at fetch time.”
5. Write provenance into every export. Include API version, authorized account, fetch timestamp, media ID, original permalink, comment sorting rule, pagination depth, and failed/missing fields.
6. Add a governance sidecar file. Alongside post.md, metadata.json, and media files, create rights_and_retention.md or governance.json covering retention period, user data fields, deletion handling, and reuse limits. CNIL’s scraping guidance makes clear that online personal-data collection needs safeguards, even where data is publicly accessible.  
7. Design for platform change. Keep Instagram-specific logic behind an adapter layer, because Meta has historically changed platform access after privacy scandals and governance pressure. In 2018, Facebook announced major restrictions on platform data access after Cambridge Analytica, and reporting at the time described sudden Instagram API access/rate-limit disruption for developers.  

The practical change is this: sell/build it as a compliance-grade export tool, not as an Instagram scraper. That changes the README, the login flow, the permission model, the error messages, and the data directory format.

A better directory would be:

instagram_export/
  DXOCAyzEX8i/
    post.md
    metadata.json
    governance.json
    media/
      reel.mp4
      thumbnail.jpg

And post.md should say something like:

Comment selection rule:
Top 10 comments by like_count among comments returned by the Instagram API
at 2026-06-26T...
This is an API-derived export, not necessarily the same ranking shown in
the Instagram mobile app UI.

⸻

5. THE FRONTIER QUESTION

Can there be a privacy-preserving, legally sanctioned, independently auditable way to retrieve a faithful snapshot of a public Instagram URL—including media, caption, and ranked comments—without relying entirely on Meta’s platform-controlled API and without violating users’ rights?

That question would change everything. If the answer becomes yes, researchers, journalists, compliance teams, and product builders could study public platform content with stronger reproducibility and lower legal risk. If the answer remains no, then every implementation must choose between two imperfect paths: a safe but platform-filtered authorized API export, or a more observational but legally and ethically fragile scraping/audit method.

# Step 4: Peer review

1. Confidence scores

Key finding from the briefing	Reliability score	Why
1. Use OAuth/API authorization, not Instagram username/password login.	9/10	This is the strongest claim. Meta’s Instagram Platform docs describe Business Login as a permission flow for accessing an Instagram professional account’s data, and Meta’s overview says access tokens follow OAuth 2.0 and are required for most API calls. Meta also says unauthorized automated collection from its platforms violates its Terms and describes enforcement against scraping.   The only reason this is not a 10 is that “research access” via Meta Content Library is a separate pathway, so “OAuth Business Login” is not the only sanctioned Meta data-access model.
2. Scope the product as authorized-account export, not arbitrary Instagram URL export.	8/10	For a commercial or ordinary app, this remains very likely right: the normal Instagram Platform is permissioned and account-based, with App Review needed for advanced permissions.   But my earlier version was too binary. Meta Content Library/API now provides qualified researchers access to public Facebook and Instagram content, including Instagram posts from business/creator accounts and verified or 100+ follower personal accounts, with Instagram accounts/posts/comments endpoints.   So the better formulation is: ordinary product = authorized-account API; qualified public-interest research = consider Meta Content Library/API; arbitrary credentialed scraper = high-risk.
3. “Top 10 comments” is a measurement choice, not a self-evident API field.	8.5/10	The confidence is high because API-accessible comment fields can support reproducible sorting rules, such as by timestamp or like count, but that is not necessarily identical to Instagram’s in-app “top comments” ranking. Meta’s comment reference exposes comment metadata such as username, text, timestamp, and like-related fields in localized docs/search snippets.   The uncertainty is that I did not verify, through a live API call, exactly which fields are returned for Reels comments under the latest app permission state.
4. Logged-out public scraping has some legal support, but that does not make logged-in credential scraping safe.	7/10	The direction is right but jurisdiction-specific. The Meta v. Bright Data summaries are quite clear that the favorable ruling was about logged-off scraping of publicly available data, not scraping behind a login wall or using a user’s Instagram credentials.   Meanwhile, Meta still publicly says unauthorized automation violates its Terms and that it blocks, rate-limits, disables accounts, and requests deletion of scraped data.   This finding needs a lawyer for any real deployment because contract, privacy, copyright, database-rights, and jurisdiction issues vary.
5. The exported directory creates rights, privacy, retention, and deletion obligations.	8.5/10	This is strongly supported for EU/France-facing use. CNIL’s 2026 guidance says collecting online personal data through scraping requires safeguards for data subjects, highlights risks around privacy, sensitive data, IP rights, erasure, and reasonable expectations, and recommends measures such as collection criteria, minimization, respecting anti-scraping signals, pseudonymization/anonymization, and objection handling.   It is slightly less certain outside the EU or for purely personal, non-commercial use, but as a product requirement it is reliable.

⸻

2. Weakest link

The weakest claim in my earlier briefing was:

“If the URL is any arbitrary public Reel/post on Instagram, there is no reliable sanctioned API path.”

That was too broad. A more accurate statement is:

For an ordinary commercial app, the sanctioned route is still authorized-account API access; but for eligible researchers, Meta Content Library/API is a sanctioned, controlled route to public Instagram content, including posts and comments. Meta says the Content Library/API provides access to the public content archive from Facebook and Instagram, includes Instagram posts from business/creator accounts and certain personal accounts, and offers Instagram accounts, posts, and comments endpoints in the API.  

To verify the corrected claim fully, I would need:

1. The exact intended use: commercial product, internal tool, academic research, nonprofit public-interest research, journalism, or personal archiving.
2. Whether the user/applicant qualifies for Meta Content Library/API access.
3. Whether Content Library/API allows lookup by Instagram permalink/shortcode or only through search/filter queries.
4. Whether it permits downloading the underlying image/video files, or only viewing/analyzing metadata/content inside a controlled environment.
5. Whether its comment fields include enough data to reconstruct the requested “top 10 comments.”
6. The export restrictions from the cleanroom environment, because SOMAR notes periodic deletion requirements for data no longer in Meta Content Library.  

⸻

3. Bias check

Yes: the Practitioner/compliance voice dominated the synthesis.

That happened because your original question was implementation-oriented: “How to get this? How does the program login?” For that kind of product question, current Meta docs, API permissions, OAuth, App Review, and Terms of Service deserve heavy weight. Meta’s Business Login and OAuth model is the most directly applicable evidence for building a normal app.  

But that weighting made the Academic/Skeptic side less prominent than it should have been. The academic evidence is not weak: researchers have argued that platform-controlled APIs restrict access, transparency, reproducibility, and scientific knowledge.   The right synthesis should separate two questions more sharply:

Product question:
What can I build safely and maintainably?
Research validity question:
Does the sanctioned data access route show what Instagram users actually see?

The first question favors the Practitioner. The second gives more weight to the Academic, Skeptic, and now the Meta Content Library angle.

⸻

4. Missing perspective

The missing sixth angle should be:

THE RESEARCH DATA ACCESS / DATA GOVERNANCE SPECIALIST

This perspective sits between the Academic, Legal, and Practitioner views. It asks: Is this a commercial export tool, a personal archive tool, or a qualified public-interest research project? That distinction changes the whole access path.

This angle would have added Meta Content Library/API earlier. Meta says Content Library/API provides comprehensive access to public Facebook and Instagram content, that applications are independently reviewed by CASD, and that the API is designed for computational researchers working in secure computing environments.   It also would have emphasized lifecycle controls: SOMAR states that some Meta Content Library API users must delete data that is no longer part of the library every 180 days, with certification requirements.  

This sixth angle changes the conclusion from a two-path model:

Official API or risky scraper

to a three-path model:

1. Authorized-account Instagram API for normal products.
2. Meta Content Library/API for eligible research.
3. Scraping only as a legally reviewed, ethically justified, last-resort research/audit method.

That is a meaningful correction.

⸻

5. Overall grade

A Stanford professor would probably give the briefing a B+.

The strengths: it identified the central issue correctly—this is not a simple scraping problem but an access-governance problem. It also gave a practical architecture, separated OAuth from credential scraping, recognized the ambiguity of “top comments,” and flagged downstream rights/privacy governance. Those are the right instincts.

The weaknesses: it was too confident in the binary between “own-account API” and “scraping,” because Meta Content Library/API creates a sanctioned research-access path for some public Instagram content. It also should have separated jurisdictions more carefully, because U.S. public-scraping case law, Meta contract enforcement, EU GDPR/CNIL guidance, and platform API rules are related but not interchangeable. Finally, it should have stated more explicitly that “top 10 comments” cannot be validated without either Meta documentation of the ranking algorithm or an empirical comparison between API output and in-app UI ranking.

What they would tell you to fix:

Turn the briefing into a decision tree by use case.

Are you exporting your own/authorized Instagram account content?
→ Use Instagram Platform / Business Login / OAuth.
Are you doing qualified academic or nonprofit public-interest research on public Instagram content?
→ Investigate Meta Content Library/API first.
Are you trying to collect arbitrary public URLs outside approved access routes?
→ Treat this as a legal, ethical, and methodological review problem before writing code.
Are you storing comments, usernames, captions, images, or videos?
→ Add retention, deletion, data-subject rights, copyright, and provenance controls.

My revised grade for the core recommendation is high. My revised grade for the completeness of the research landscape is lower, because Meta Content Library/API should have been included earlier as the most important missing middle path.