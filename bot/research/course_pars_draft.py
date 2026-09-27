"""Draft par database for the Golf+ Discord bot's scorecard feature.

Researched 2026-09-25 from official/authoritative sources. Each entry was
sanity-checked: the 18 hole pars sum to the course's officially stated total.
Verified via independent parallel research workers; every entry cross-checked
against at least two sources (source shown is the primary).

Not yet wired into the bot — integration into src/golfplus_courses.py and
/tournament create auto-fill is the next step (parent agent's call).
"""

COURSE_PARS = {
    # Source: https://en.wikipedia.org/wiki/Valhalla_Golf_Club (total par 72, standard course card; Gold tees = championship)
    # Note: the 2024 PGA Championship tournament setup played hole 2 as par 4 (total 71); standard card used here.
    "Valhalla Golf Club": [4, 5, 3, 4, 4, 4, 5, 3, 4, 5, 3, 4, 4, 3, 4, 4, 4, 5],

    # Source: https://www.golfify.io/courses/wolf-creek-golf-course-mesquite (total par 72, Challenger/championship tees)
    # Note: this is the Mesquite, Nevada desert course (the one in Golf+); "Wolf Creek" alone is ambiguous.
    "Wolf Creek Golf Club": [5, 4, 3, 4, 5, 4, 4, 3, 4, 4, 3, 5, 4, 4, 3, 4, 5, 4],

    # Source: https://golfsherpa.co.uk/courses/scotland/st-andrews-golf-links-old (total par 72, Blue/championship tees, men's card)
    # Note: the ladies' card plays holes 2, 4, 13, 17 as par 5 (total 76) — not used.
    "The Old Course at St Andrews": [4, 4, 4, 4, 5, 4, 4, 3, 4, 4, 3, 4, 4, 5, 4, 4, 4, 4],

    # Source: https://thegolfnewsnet.com/golfnewsnetteam/2026/02/09/pebble-beach-golf-links-spyglass-hill-golf-course-scorecard-and-course-breakdown-for-2026-att-pebble-beach-pro-am-hosts-140575/ (total par 72, 2026 AT&T Pebble Beach Pro-Am scorecard; par row identical on all tee sets)
    "Pebble Beach Golf Links": [4, 5, 4, 4, 3, 5, 3, 4, 4, 4, 4, 3, 4, 5, 4, 4, 3, 5],

    # Source: https://www.golfify.io/courses/pinehurst-resort-country-club-2 (total par 72, Blue tees, standard regular-play card)
    # Note: U.S. Open championship setup is par 70 (excluded). Holes 4/5 swapped par designations before 2014; current card has 4 = par 4, 5 = par 5.
    "Pinehurst No. 2": [4, 4, 4, 4, 5, 3, 4, 5, 3, 5, 4, 4, 4, 4, 3, 5, 3, 4],

    # Source: https://www.hole19golf.com/courses/kiawah-island-golf-resort-ocean (total par 72, Black/championship tees)
    "The Ocean Course at Kiawah Island": [4, 5, 4, 4, 3, 4, 5, 3, 4, 4, 5, 4, 4, 3, 4, 5, 3, 4],

    # Source: https://thegolfnewsnet.com/golfnewsnetteam/2026/03/10/tpc-sawgrass-players-stadium-course-scorecard-and-course-breakdown-for-2026-the-players-championship-host-141168/ (total par 72, THE PLAYERS Stadium Course championship tees; identical par row on all tee sets; corroborated by Wikipedia)
    "TPC Sawgrass": [4, 5, 3, 4, 4, 4, 4, 3, 5, 4, 5, 4, 3, 4, 4, 5, 3, 4],

    # Source: https://en.wikipedia.org/wiki/TPC_Scottsdale (total par 71, Stadium Course championship/Black tees; identical par row on all tee sets)
    "TPC Scottsdale": [4, 4, 5, 3, 4, 4, 3, 4, 4, 4, 4, 3, 5, 4, 5, 3, 4, 4],

    # Source: https://www.pgatour.com/article/news/latest/2026/08/11/fedex-st-jude-championship-tpc-southwind-course-overview-memphis-tennessee (total par 70, PGA Tour championship tees / FedEx St. Jude setup; corroborated by PGA Tour official scorecard PDF and tpc.com)
    # Ambiguity: some member tee rows play holes 5 and 10 as par 5 (course par 71). Championship-tee par 70 used here per preference for tournament framing.
    "TPC Southwind": [4, 4, 5, 3, 4, 4, 4, 3, 4, 4, 3, 4, 4, 3, 4, 5, 4, 4],

    # Source: https://en.wikipedia.org/wiki/Riviera_Country_Club (total par 71, men's par row covering championship/back tees; corroborated by USGA 2026 U.S. Women's Open fact sheet)
    "The Riviera Country Club": [5, 4, 4, 3, 4, 3, 4, 4, 4, 4, 5, 4, 4, 3, 4, 3, 5, 4],

    # Source: https://www.pgatour.com/tournaments/2025/the-sentry/R2025016/course-stats (total par 73, championship/Tour setup, The Sentry)
    # Note: hole 7 is par 4 on the men's championship row (some sites list it as par 5 for forward/ladies' tees, total 74).
    "Kapalua Plantation Course": [4, 3, 4, 4, 5, 4, 4, 3, 5, 4, 3, 4, 4, 4, 5, 4, 4, 5],

    # Source: https://pgatour-uat.pgatour.com/tournaments/2025/tour-championship/R2025060/course-stats (total par 70, TOUR Championship setup)
    # Par history: par 71 in 2024 (post-restoration), reverted to par 70 for 2025+ (hole 14 back to par 4). The club's own site still shows an older par-72 member card — tournament setup used here.
    "East Lake Golf Club": [4, 3, 4, 4, 4, 5, 4, 4, 3, 4, 3, 4, 4, 4, 3, 4, 4, 5],

    # Source: https://www.pgatour.com/article/news/needtoknow/2023/08/11/five-things-to-know-about-olympia-fields-north-course-bmw-championship-fedexcup-playoffs-chicago (total par 70, North Course championship/BMW Championship tees)
    # Note: some member tee rows list hole 18 as par 4/5 (total 70/71); championship row used here.
    "Olympia Fields Country Club": [5, 4, 4, 4, 4, 3, 4, 3, 4, 4, 4, 4, 3, 4, 5, 3, 4, 4],

    # Source: https://en.wikipedia.org/wiki/Yale_Golf_Course (total par 70; single par row applies to all tee sets, championship tees 6,825 yds)
    "Yale Golf Course": [4, 4, 4, 4, 3, 4, 4, 4, 3, 4, 4, 4, 3, 4, 3, 5, 4, 5],

    # Source: https://thegolfnewsnet.com/golfnewsnetteam/2026/04/14/harbour-town-golf-links-scorecard-and-course-breakdown-for-2026-rbc-heritage-host-142198/ (total par 71, RBC Heritage tournament tees; corroborated by allsquaregolf, offcourse.co, mscorecard.com)
    "Harbour Town Golf Links": [4, 5, 4, 3, 5, 4, 3, 4, 4, 4, 4, 4, 4, 3, 5, 4, 3, 4],

    # Source: https://www.pgatour.com/tournaments/2026/arnold-palmer-invitational-presented-by-mastercard/R2026009/course-stats (total par 72, 2026 Arnold Palmer Invitational tournament tees; same par row on all member tees)
    "Bay Hill Club & Lodge": [4, 3, 4, 5, 4, 5, 3, 4, 4, 4, 4, 5, 4, 3, 4, 5, 3, 4],

    # Source: https://www.pgatour.com/tournaments/2024/bmw-championship/R2024028/course-stats (total par 72, 2024 BMW Championship tournament tees; same par row on all tee rows)
    # Note: verified this is Castle Pines Golf Club itself, NOT "The Country Club at Castle Pines" (a different course with different pars).
    "Castle Pines Golf Club": [5, 4, 4, 3, 4, 4, 3, 5, 4, 4, 3, 4, 4, 5, 4, 3, 5, 4],

    # Source: https://www.hole19golf.com/courses/lofoten-links (total par 71; par row identical on all four tee sets; official site lofotenlinks.no states Par 71 but publishes hole detail only as images)
    # Note: hole-by-hole numbers corroborated by three agreeing scorecard databases (Hole19, allsquaregolf, golftraxx); a garbled golfify.io par column was disregarded.
    "Lofoten Links": [4, 3, 4, 4, 4, 3, 5, 5, 4, 4, 4, 3, 5, 4, 4, 4, 3, 4],
}

# No courses omitted — all 18 verified against authoritative sources with matching totals.
