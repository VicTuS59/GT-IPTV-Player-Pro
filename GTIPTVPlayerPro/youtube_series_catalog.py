# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Full-episode playlists and bundled episodes from official YouTube publishers."""

from typing import NamedTuple


# ISO 3166-1 alpha-2 assignments, checked against the public-domain tzdata table.
COUNTRY_CODES = tuple("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
DE DJ DK DM DO DZ
EC EE EG EH ER ES ET
FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
HK HM HN HR HT HU
ID IE IL IM IN IO IQ IR IS IT
JE JM JO JP
KE KG KH KI KM KN KP KR KW KY KZ
LA LB LC LI LK LR LS LT LU LV LY
MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
NA NC NE NF NG NI NL NO NP NR NU NZ
OM
PA PE PF PG PH PK PL PM PN PR PS PT PW PY
QA
RE RO RS RU RW
SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
UA UG UM US UY UZ
VA VC VE VG VI VN VU
WF WS
YE YT
ZA ZM ZW
""".split())


class YouTubeEpisode(NamedTuple):
    title: str
    video_id: str


class YouTubeSeries(NamedTuple):
    title: str
    publisher: str
    episodes: tuple[YouTubeEpisode, ...]
    source_url: str
    playlist_ids: tuple[str, ...] = ()
    channel_id: str = ""
    minimum_episode_seconds: int = 600
    episode_series: str = ""
    title_aliases: tuple[str, ...] = ()
    channel_uploads: bool = False
    current: bool = False


def _turkish_series(title, publisher, channel_id, first_episode, playlists=()):
    return YouTubeSeries(
        title, publisher, (YouTubeEpisode("1. Bölüm", first_episode),),
        "https://www.youtube.com/channel/" + channel_id, playlists, channel_id, 2700,
        title_aliases=(title,), channel_uploads=not playlists,
    )


# A title can have a publisher's full-episode playlists and bundled episode
# fallbacks. Availability may vary by territory and change over time.
SERIES_BY_COUNTRY = {
    "AU": (
        YouTubeSeries(
            "Round the Twist", "Australian Children's Television Foundation",
            (
                YouTubeEpisode("S1 E1 · Skeleton on the Dunny", "14PA6o2md00"),
                YouTubeEpisode("S1 E2 · Birdsdo", "vJMoJKLen2o"),
                YouTubeEpisode("S1 E3 · A Good Tip for Ghosts", "l1F2b3DVXeM"),
                YouTubeEpisode("S1 E5 · Spaghetti Pig Out", "Sx-wyPEAXuI"),
                YouTubeEpisode("S1 E6 · The Gum Leaf War", "PJItt__KsYo"),
                YouTubeEpisode("S1 E11 · The Copy", "0aLRtE41saA"),
                YouTubeEpisode("S1 E12 · Without My Pants", "xJ9aCrumBrY"),
            ),
            "https://education.actf.com.au/news/free-actf-content-and-resources-for-teachers",
            ("PLl48jMfth9b8RF2obC5sBaQpXeyQOGUIT",),
        ),
    ),
    "BR": (
        YouTubeSeries(
            "Castelo Rá-Tim-Bum", "TV Cultura",
            (YouTubeEpisode("Episódio 1", "j-uldPC8iTg"),),
            "https://cultura.uol.com.br/playlists/5_castelo-ra-tim-bum-episodios-completos.html",
            ("PL2-ncQsY8gXXoogNdIizbOHl29MMjon5j",),
        ),
    ),
    "CA": (
        YouTubeSeries(
            "The Red Green Show", "Red Green / S&S Productions",
            (
                YouTubeEpisode("S1 E1 · The Big Outboard", "FUXPuYZ4DEQ"),
                YouTubeEpisode("S1 E2 · The Elvis Sighting", "HbOX7yyL8VM"),
            ),
            "https://redgreen.com/pages/watch",
        ),
    ),
    "DE": (
        YouTubeSeries(
            "K11 – Die neuen Fälle", "SAT.1",
            (
                YouTubeEpisode("Mord an 21-jähriger Studentin", "L5QQSXKwXBY"),
                YouTubeEpisode("Gefesselt ans Laufband", "knjPQzvW8IQ"),
                YouTubeEpisode("Mann entführt & Schwangere Ehefrau in Lebensgefahr", "kXSA17cSbc0"),
            ),
            "https://www.joyn.de/serien/k11-die-neuen-faelle",
        ),
    ),
    "ES": (
        YouTubeSeries(
            "Pocoyó", "Pocoyó / Animaj",
            (
                YouTubeEpisode("¡Vamos al supermercado!", "F3hWh5wBs7U"),
                YouTubeEpisode("¡Pocoyó y los superalimentos!", "hZVkO0B11-U"),
            ),
            "https://www.pocoyo.com/es/episodios",
        ),
    ),
    "FR": (
        YouTubeSeries(
            "Oggy et les Cafards", "Xilam Animation",
            (YouTubeEpisode("S4 E26 · Oggy champion de sport", "z1I7nBwa9z4"),),
            "https://xilam.com/media/oggy-et-les-cafards/",
        ),
        YouTubeSeries(
            "Samuel", "ARTE",
            (YouTubeEpisode("01 · La Grande Julie", "woIaitMj4FM"),),
            "https://www.arte.tv/fr/videos/RC-024878/samuel/",
        ),
    ),
    "GB": (
        YouTubeSeries(
            "Mr. Bean", "Mr Bean / Tiger Aspect",
            (
                YouTubeEpisode("Mr. Bean", "YEjMK9DB4no"),
                YouTubeEpisode("The Return of Mr. Bean", "r1NsFQUZhb8"),
                YouTubeEpisode("The Curse of Mr. Bean", "K_bX_jX9O8w"),
                YouTubeEpisode("Mr. Bean Goes to Town", "dUU7RAiLWrk"),
                YouTubeEpisode("The Trouble with Mr. Bean", "YKv90WGMtGI"),
                YouTubeEpisode("Mr. Bean Rides Again", "Rz_Hj-Fo-EU"),
                YouTubeEpisode("Merry Christmas, Mr. Bean", "L2UCRNldC3s"),
                YouTubeEpisode("Mr. Bean in Room 426", "Yi9207WmpTc"),
                YouTubeEpisode("Mind the Baby, Mr. Bean", "JFf2GqonpR0"),
                YouTubeEpisode("Do-It-Yourself Mr. Bean", "IqFGj837W9A"),
                YouTubeEpisode("Tee Off, Mr. Bean", "WDrlalXfxec"),
                YouTubeEpisode("Goodnight Mr. Bean", "A_S0PQkwO5E"),
                YouTubeEpisode("Hair by Mr. Bean of London", "hz_e7SRecWw"),
            ),
            "https://www.mrbean.com/",
        ),
    ),
    "IN": (
        YouTubeSeries(
            "Crime Patrol", "SET India",
            (
                YouTubeEpisode("एक भयंकर अपराध", "WZoGiMKePjU"),
                YouTubeEpisode("क्रूर षड्यंत्र", "aXJwYvwxens"),
            ),
            "https://www.youtube.com/watch?v=WZoGiMKePjU",
        ),
        YouTubeSeries(
            "महाभारत (Mahabharat, 1988)", "Pen Bhakti",
            (),
            "https://lnk.bio/PenMovies",
            ("PLa6CHPhFNfadNcnVZRXa6csHL5sFdkwmV",),
        ),
    ),
    "IT": (
        YouTubeSeries(
            "Winx Club", "Rainbow",
            (
                YouTubeEpisode("S1 E1 · Una fata a Gardenia", "An6vdwMNXjA"),
                YouTubeEpisode("S1 E2 · Benvenuti a Magix", "D0c-ttez0pM"),
                YouTubeEpisode("S1 E3 · L'anello di Stella", "VJo7DNHHZ9c"),
            ),
            "https://www.winxclub.com/it",
            (
                "PLpxUE6pxFtk5UaEhEwG2YnROOp0FqoNCZ",
                "PLpxUE6pxFtk4bCBZV9hxPYMElVOW0Kap6",
                "PLpxUE6pxFtk65tHX6wEWCDklBDnM8drTs",
                "PLpxUE6pxFtk5-3tN5MD4UTIhRKk62nzOW",
                "PLpxUE6pxFtk6YSXMyEg7H-xtH-gGg8azM",
                "PLpxUE6pxFtk7v0IgoLLlPFdGHkE2IMaiJ",
                "PLpxUE6pxFtk5HwM4D-xBqdbH3bFOsYbL5",
                "PLpxUE6pxFtk5CW8_Gzz-FLqZSsq8GPpk3",
            ),
        ),
    ),
    "JP": (
        YouTubeSeries(
            "キカイダー01 (Kikaider 01)", "Toei",
            (
                YouTubeEpisode("第1話", "kvzij-1d7HU"),
                YouTubeEpisode("第2話", "m7oljAG-Fiw"),
            ),
            "https://www.youtube.com/@TTY0",
        ),
    ),
    "KR": (
        YouTubeSeries(
            "아버지가 이상해 (My Father is Strange)", "KBS WORLD TV",
            (
                YouTubeEpisode("1회", "T8kjbrFI1eo"),
                YouTubeEpisode("2회", "93PqBCWM8uo"),
            ),
            "https://kbsworld.kbs.co.kr/program/view.php?pg_seq=1108",
        ),
    ),
    "MY": (
        YouTubeSeries(
            "Upin & Ipin", "Les' Copaque Production", (),
            "https://www.youtube.com/playlist?list=PLdm4JCUOHx8701zpqOlb016MkKs7Gg-2d",
            ("PLdm4JCUOHx8701zpqOlb016MkKs7Gg-2d",),
            "UCR1c65UsjpaVgcLKa7eM1tg", 420, title_aliases=("Upin & Ipin",),
        ),
    ),
    "NL": (
        YouTubeSeries(
            "Woezel & Pip", "Woezel & Pip",
            (YouTubeEpisode("Ik zie, ik zie, wat jij niet ziet", "Cu8cW02BV1I"),),
            "https://www.woezelenpip.nl/kijken-en-luisteren/filmpjes",
        ),
    ),
    "PH": (
        YouTubeSeries(
            "Wansapanataym: Mr. CutePido", "ABS-CBN Entertainment",
            (
                YouTubeEpisode("Episode 1", "tEhJKNlrkMw"),
                YouTubeEpisode("Episode 2", "ffNpKaOdFzE"),
                YouTubeEpisode("Episode 3", "OR0gxs-hkh0"),
                YouTubeEpisode("Episode 4", "9mh62d80W7k"),
                YouTubeEpisode("Episode 5", "sulLRIzKFD8"),
                YouTubeEpisode("Episode 6", "jQ_8lcN0jj0"),
                YouTubeEpisode("Episode 7", "QMSWxo7X8yc"),
                YouTubeEpisode("Episode 8", "ntCfcJoS49I"),
                YouTubeEpisode("Episode 9", "pKSKru3Ctag"),
                YouTubeEpisode("Episode 10", "_nv4NWfzE5A"),
                YouTubeEpisode("Episode 11", "c79gGxEThTo"),
            ),
            "https://www.youtube.com/watch?v=tEhJKNlrkMw",
        ),
    ),
    "PK": (
        YouTubeSeries(
            "Betiyaan", "ARY Digital",
            (YouTubeEpisode("Episode 1", "KJ7UL2pUzVI"),),
            "https://arydigital.tv/betiyaan-drama-plot/",
            ("PLb2aaNHUy_gEzvVu-V2VUNmeQW9uW5bTD",),
        ),
        YouTubeSeries(
            "Fraud", "ARY Digital",
            (YouTubeEpisode("Episode 1", "lX55POvBUFM"),),
            "https://arydigital.tv/drama/fraud/",
            ("PLb2aaNHUy_gGpVzaNraEGeJ4j4W54snEi",),
        ),
    ),
    "TR": (
        _turkish_series("Eşref Rüya", "Kanal D / TİMS&B", "UCFQ6ddDglWtXpPK3Qmn3k1w", "IWZr0PdZfSM"),
        _turkish_series("Kızılcık Şerbeti", "Show TV", "UCRfLDCtkSwmTdwHrbmC78Xg", "X_yBRarj8lE"),
        _turkish_series("Daha 17", "Kanal D", "UC9qIbva82rrHzSM1kI7Cb6g", "qYvM26WeYeg"),
        _turkish_series("Güller ve Günahlar", "Kanal D", "UCalPDcmRPlpaaXgNg8csL5w", "jpMmM0brbQc"),
        _turkish_series("Yalı Çapkını", "Star TV / OGM Pictures", "UCyahIZNtNmqK1LeNF0KlFZw", "-u_RlLqmopg",
                        ("PL3qZLHWaEvNntnJfPfTTspDoOKE0sKLnU", "PL3qZLHWaEvNkOBlctTs1zWYBuCVsR8Nxa")),
        _turkish_series("Bahar", "Show TV / MF Yapım", "UCYN-z1B7Luc54FFmgdExjnw", "QeWGR13YGfU",
                        ("PLbMRP6ZEJPm4",)),
        _turkish_series("İnci Taneleri", "Kanal D / BKM", "UCUxSoTMNflf9TAlZbml7XMw", "mS6uenrsxkc",
                        ("PLlpSU4y2gAeU8KwVgTPwdBeSkouuOjyut",)),
        _turkish_series("Kızıl Goncalar", "NOW", "UCvMf-BavX_3YjG-JPrpPqLw", "zRR-WxH0qZ4"),
        _turkish_series("Hudutsuz Sevda", "NOW", "UChE38WMhQs2YCv9IueqkhPg", "h-L_lb_OouU",
                        ("PLkAJZR6yRPI3EXcBrL76bE33wZ16rxzho",)),
        _turkish_series("Teşkilat", "TRT 1", "UCt8FO3MT1fWD-UI7hnutj8A", "783_OJX_1fA"),
        _turkish_series("Çukur", "Show TV / Ay Yapım", "UCTAkwLbVzHxsbgsp6cRsPDg", "XbT2CoTGBdo",
                        ("PL_8aRB2NmwZ6dWErHPBcpjml7AS6LATWA",)),
        _turkish_series("Aile", "Show TV / Ay Yapım", "UC5d3mcgb1Fhn5KNfpNTKtnQ", "L87JK5llZw8",
                        ("PLDk2DVQuW9PqNBWss8eG86skF1rgelfN6",)),
        _turkish_series("Sen Çal Kapımı", "NOW / MF Yapım", "UC_o475bQjzx17732_6P5vdA", "14enyXXZrrU",
                        ("PLFnn1C5aaJ8bJQApinNe1dBLuSXtNr63D",)),
        _turkish_series("Yasak Elma", "NOW", "UC5TccbGxEeVMx9gL6wp9fOg", "Bg6idO0U7d8"),
        _turkish_series("Kara Sevda", "Star TV / Ay Yapım", "UCo4pRMCvQXDqXgDChYzYw9Q", "fVNzewv_2oU",
                        ("PLwjf4vKFw7V0L0FKF4JDy8KMEHDC9Uyzb",)),
        _turkish_series("Arka Sokaklar", "Kanal D / Erler Film", "UCJBBp-6JoqjOeLzTxzol_7A", "TBBRBD8Fudg",
                        ("PLpkURSHo_FmIF6Xq1eGXmM8g35A6VdRr1",)),
        YouTubeSeries(
            "Diriliş Ertuğrul", "TRT 1",
            (YouTubeEpisode("1. Bölüm", "kypIEGvq_30"),),
            "https://www.trt1.com.tr/diziler/dirilis-ertugrul/bolum/1-bolum",
            channel_id="UCrr2fV8yd6r8k-FZKGxDr9Q", minimum_episode_seconds=2700,
            title_aliases=("Diriliş Ertuğrul",), channel_uploads=True,
        ),
        YouTubeSeries(
            "Yargı", "Ay Yapım / Kanal D",
            (
                YouTubeEpisode("1. Bölüm", "KqxAM9FNT-I"),
                YouTubeEpisode("4. Bölüm", "D24426SLy1U"),
            ),
            "https://www.kanald.com.tr/yargi/bolumler",
            ("PLqPknCRryunFbxrBlMftfOqLolEqcPDCL",), "UCN5uB_vZCRnlTQIeHgjeMug", 2700,
            title_aliases=("Yargı",),
        ),
        YouTubeSeries(
            "Uzak Şehir", "Ay Yapım / Kanal D",
            (YouTubeEpisode("1. Bölüm", "Tz6J5B0as2Y"),),
            "https://www.kanald.com.tr/uzak-sehir/bolumler",
            ("PL49MEhd9Gx8HdR7VCgSXWMX7ypd685YZr",),
        ),
        YouTubeSeries(
            "Kuruluş Osman", "Bozdağ Film / atv",
            (YouTubeEpisode("1. Bölüm", "0fTJyCTwznM"),),
            "https://www.atv.com.tr/haberler/2023/05/26/atv-youtube-canli-izle-atv-tum-icerikleriyle-youtubeda-atv-canli-yayin-ile-hicbir-icerigi-kacirma",
            ("PLCuVYHE7O2A0XGkdhaU_M4-IkEIq5hSOQ",),
        ),
        YouTubeSeries(
            "Kardeşlerim", "atv", (),
            "https://www.atv.com.tr/haberler/2023/05/26/atv-youtube-canli-izle-atv-tum-icerikleriyle-youtubeda-atv-canli-yayin-ile-hicbir-icerigi-kacirma",
            ("PLTLp6QTqSRy8ZpihQ0c4U9CJmTLxYBPG2",),
        ),
        YouTubeSeries(
            "Ezel", "Ay Yapım / atv", (),
            "https://www.atv.com.tr/ezel",
            ("PLsvJ8lYSMnLufc5q0mYY_JsQS7-oplO0r",),
        ),
        YouTubeSeries(
            "Gönül Dağı", "TRT 1", (),
            "https://www.trt1.com.tr/diziler/gonul-dagi/bolum",
            ("PLskt8bvhVjrUbynLtGtzHAvpArB7sWFVv",),
        ),
        YouTubeSeries(
            "Payitaht Abdülhamid", "TRT 1", (),
            "https://www.trt1.com.tr/diziler/payitaht-abdulhamid",
            ("PLge_kMuGwvL8pfyOpLP0hXprI7yQ0L2q-",),
        ),
    ),
    "US": (
        YouTubeSeries(
            "Arthur", "PBS KIDS",
            (
                YouTubeEpisode("Cents-less", "2JMYazJC5ys"),
                YouTubeEpisode("Lend Me Your Ear / The Butler Did It (ASL)", "AXJWluMm2vM"),
            ),
            "https://pbskids.org/videos/arthur",
        ),
    ),
}
