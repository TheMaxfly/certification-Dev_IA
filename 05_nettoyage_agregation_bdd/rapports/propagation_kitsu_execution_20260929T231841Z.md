# Propagation du kitsu_id par identifiant — exécution

Horodatage : `20260929T231841Z` · durée 0.9 s · **écrit et commité**

## Environnement

- Serveur : PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
- Connexion : `dbname=apimanga host=localhost port=5432 user=postgres`
- Base / rôle : `apimanga` / `postgres`
- Migrations appliquées : 19 (dernière : 018)
- Python / psycopg : 3.12.3 / 3.3.4
- Code : `36cffb2 + modifications non commitées`

## La règle

Pures jointures d'identifiants (`src/identity/sql/propagation_kitsu.sql`). Une série est rattachée si, cumulativement : elle porte un MAL ou un AniList issu d'une décision antérieure de la cascade, automatique ou arbitrée ; les correspondances Kitsu mènent de ces identifiants à **exactement une** entrée ; cette entrée est manga, manhwa ou manhua ; elle n'est rattachée à aucune autre série ; le `kitsu_id` de la série est vide. **L'égalité de titre n'est pas une condition** : elle est mesurée ci-dessous comme confirmation.

## Chiffres de contrôle

| # | Contrôle | Attendu | Mesuré |
|---|---|---|---:|
| 1 | séries candidates (kitsu_id vide, identifiant externe présent) | ≥ 1 168 au premier run | 1 385 |
| 2 | rattachements effectués | à produire | **1 168** |
| 3 | dont confirmés par égalité de titre | ≈ 1 073, mesuré | 1 094 |
| 4 | canaris One Piece → 38, Naruto → 35, Death Note → 57, Monster → 4 | rattachés | ✅ les quatre |
| 5 | doublons légitimes touchés | 0 | 0 |
| 6 | `kitsu_id` déjà renseignés modifiés | 0 | 0 |
| 7 | atteignabilité | ~8 718 au premier run | 8 121 → **8 718** (59,4 %) |

Répartition des rattachements par décision source : `exact` 86, `exact_author` 900, `llm_review` 182. Types d'entrée : manga 1 164, manhua 2, manhwa 2.

**N° 3, la définition écrite.** Une entrée déduite est confirmée si l'un de ses titres Kitsu (canonique, variantes, abrégés) égale, après `normaliser()`, le titre ou un alias de la série — la définition de `evaluation.catalogue`, celle de `confirmer`. Le « 1 073 » du §63.5 comptait la même égalité sur une base plus étroite, les seules entrées du corpus non rattachées : sur cette base, 1 072 rattachements, plus 1 série(s) en conflit dont l'identifiant mène aussi à l'entrée, sans y mener seul. Même mesure, base plus large ; pas un écart de règle.

## Classement des candidates

| Cas | Séries |
|---|---:|
| `rattachable` | 1 168 |
| `sans_chemin` | 216 |
| `conflit_plusieurs_entrees` | 1 |
| `hors_type` | 0 |
| `conflit_deja_rattachee` | 0 |
| `conflit_entree_partagee` | 0 |
| **total** | **1 385** |

`sans_chemin` par décision source : `exact` 25, `exact_author` 158, `llm_review` 33.

## Conflits

**Point A — un identifiant mène à plusieurs entrées Kitsu.** Exclues et listées : on ne choisit pas (décision du 2026-09-30).

| series_id | Titre | Décision source | MAL | AniList | Entrée Kitsu | Type | Titre Kitsu | Rattachée à |
|---:|---|---|---|---|---:|---|---|---|
| 52657 | Chronos Ruler | `llm_review` | 91050 | 95410 | 36209 | manga | Chronos Ruler | — |
| 52657 | Chronos Ruler | `llm_review` | 91050 | 95410 | 62822 | manhua | Shijian Zhipei Zhe | — |

**Point B — entrée déjà rattachée à une autre série, ou déduite par deux séries.** Arrêt s'il y en a.

Aucun.

## Contrôles après écriture (dans la transaction)

| Contrôle | Valeur |
|---|---:|
| kitsu_id sur deux séries | 0 |
| série sur deux lignes d'identité | 0 |
| kitsu_id déjà renseignés modifiés | 0 |
| rattachements sans décision | 0 |

| Moyeu | Avant | Après |
|---|---|---|
| empreinte md5 de `work_identity` | `64234283499d0f2c3922e50e9ddd6599` | `e45c028a59eb7063183e98eae0771064` |
| décisions au journal | 10 347 | 11 515 |
| dernier `decision_id` | 33014 | 35350 |

## Ce que dit l'égalité de titre (§63.5, sur l'état d'avant)

| | |
|---|---:|
| entrées Kitsu du corpus non rattachées | 30 454 |
| dont à titre égal à une série du catalogue | 2 531 |
| — doublons (toutes les séries à titre égal ont déjà un kitsu_id) | 474 |
| — rattachements manqués | 2 057 |
| séries concernées par un rattachement manqué | 1 848 |
| — rattachées par cet étage | 1 079 |
| — en conflit (points A, B) | 1 |
| — **hors périmètre : aucun chemin par identifiant** | **768** |

## Hors périmètre — titre égal, aucun chemin par identifiant

**768 séries.** Cet étage ne rattache jamais par le titre : ces séries restent à instruire (étage 2). Entrées Kitsu non rattachées dont un titre égale celui de la série :

| series_id | Titre | Entrées Kitsu à titre égal |
|---:|---|---|
| 740 | Cowboy Bebop | 414 |
| 743 | Blade of the Phantom Master - Le nouvel Angyo Onshi | 1621, 66044 |
| 903 | Les Bijoux | 859 |
| 934 | Chunchu | 6954 |
| 935 | Eternity | 719 |
| 942 | Kizuna | 3358, 9754 |
| 1080 | Vision d'Escaflowne | 1285, 19361 |
| 1096 | Dispersion | 19277 |
| 1145 | IWGP  - Ikebukuro West Gate Park | 599 |
| 1152 | Beautiful World | 7498, 36247 |
| 1205 | L'Anneau des Nibelungen | 23857 |
| 1235 | Daiô | 18876 |
| 1251 | Gasaraki | 6442 |
| 1268 | Hunter | 54508 |
| 1309 | Let's Volley Ball ! | 72704 |
| 1391 | Ragnarok | 2913, 20538 |
| 1394 | Rampou | 674 |
| 1398 | Red Eyes | 2379 |
| 1401 | Ring | 983, 4974, 6742, 6747, 12453, 59820, 67297 |
| 1408 | Ryu Seiki | 14808 |
| 1428 | Sister | 31629, 35953 |
| 1435 | Star Wars | 2403, 36872 |
| 1453 | Goldorak (Nagai - Ota) | 6220 |
| 1461 | La Vie en Rose | 651, 10213, 15337, 49964 |
| 1511 | Angry | 19900 |
| 1548 | Samurai Rising | 6123 |
| 1560 | Twelve | 19363 |
| 1563 | Japan | 2113, 3491 |
| 1616 | Xs | 3322 |
| 1625 | Cafe Occult | 3874 |
| 1645 | One | 1059, 75899 |
| 1881 | Avec Karine | 6506 |
| 1922 | Reset | 4313, 8995, 10048, 20203, 22335, 33674 |
| 1929 | PI | 24440, 24954 |
| 1936 | Zero | 2686, 8579, 9254, 10516, 25518 |
| 1950 | G+ | 13033, 76808 |
| 1977 | Crash | 8675, 13847, 16018, 73092 |
| 2008 | Angel | 3144, 10347, 36758 |
| 2257 | Heroes | 35830 |
| 2258 | Nora | 17652, 34452 |
| 2286 | Le Roi Venu d'Ailleurs | 7106 |
| 2328 | Les Vents de la Colère | 39058 |
| 2337 | Good Bye | 15153, 19014 |
| 2384 | Zetman [one-shot] | 3262 |
| 2464 | Brothers | 4090, 6378, 24419, 26438 |
| 2603 | The Ghost in the Shell 1.5 | 2239 |
| 2624 | Le Vent du Nord est Comme le Hennissement d’un Cheval Noir | 22770 |
| 2627 | Déclic Sensuel | 9850 |
| 2728 | Cobra - Couleur | 2302, 19032 |
| 2733 | Orange | 8531, 13243, 18285, 24892 |
| 2734 | Dream | 8553 |
| 2737 | Golden Man | 71876 |
| 2866 | Step | 1587, 2754, 6565, 15732 |
| 2880 | Emperor's Castle | 20482 |
| 3372 | L'Idiot | 8243 |
| 3927 | Please Teacher | 1902, 19137 |
| 4745 | Ray + Other Side | 6163 |
| 4934 | Go ! Go ! Heaven | 3236 |
| 5055 | Free Fight - New Tough | 7145 |
| 5056 | L'Amour en Cours | 78601 |
| 5063 | Bienvenue dans la NHK! | 1048 |
| 5256 | Toto, The Wonderful Adventure | 15520 |
| 5269 | Mazinger Z | 17791 |
| 5276 | Les Secrets de l'Economie Japonaise | 5856 |
| 5289 | Temptation | 5612, 34464 |
| 5293 | La Montagne Magique | 11260 |
| 5382 | Castlevania | 16551 |
| 5502 | Stigmata | 71407 |
| 5533 | L'Enfer de Jade | 36812 |
| 5577 | High School Paradise | 35964 |
| 5593 | Star Wars - Silver & Black | 10033, 10204 |
| 5599 | Color | 426, 13201 |
| 5640 | The Moon | 25955 |
| 5649 | .Hack// G.U. + | 299 |
| 5658 | Shônen Shôjo | 9419, 27269, 36632 |
| 5747 | Contes du Japon d'autrefois | 22402 |
| 5774 | Scrapped princess | 1184 |
| 5845 | Otomen | 1911 |
| 5865 | It's Your World | 11059 |
| 5875 | La Recette de l'Amour | 6019 |
| 5883 | Big Bang Vénus | 6975 |
| 5895 | Shin Angel | 3144, 10347, 36758 |
| 5986 | La Submersion du Japon | 20531, 27645 |
| 6013 | Clover | 293, 2053, 22004, 24953 |
| 6054 | Rosario + Vampire - Saison II | 7435 |
| 6169 | Othello | 664, 944, 14147, 20157 |
| 6201 | Hare Guu | 10108 |
| 6354 | Nagaraja | 6269 |
| 6368 | Suna no Rasen | 5738 |
| 6415 | Journaliste | 1124 |
| 6513 | Compte à rebours | 10042, 15469, 22841 |
| 6593 | Hokuto no Ken - La Légende de Julia | 8821 |
| 6630 | Sumanai!! Masumi kun | 21929 |
| 6662 | World's End | 350 |
| 6675 | La Mosca | 8086, 22235 |
| 6696 | Hiroshima | 31687 |
| 6796 | A Romantic Love Story | 9440 |
| 6855 | Dragon Quest - Emblem of Roto | 12459 |
| 6888 | Wanted | 1745, 2608, 4437 |
| 6901 | Dear - Cocoa Fujiwara | 7672, 9810, 32333 |
| 6908 | Koukaku no Regios | 15271 |
| 6932 | Fatal Fury | 16135 |
| 6963 | Switch | 2908, 8891, 33566, 41064, 66384 |
| 6969 | Pat Ken | 15551 |
| 7003 | Kanon | 1410, 3846, 9181, 13049 |
| 7035 | China girls | 21369 |
| 7061 | The Sacred Blacksmith | 22512 |
| 7071 | Princess Collection | 9602 |
| 7085 | In the end | 22484 |
| 7091 | Les Fruits Sanglants [Junji Ito Collection n°7] | 24354, 63608, 75801 |
| 7143 | Grimms Manga | 15892, 52682 |
| 7149 | Reminiscences | 15044 |
| 7161 | Le samouraï bambou | 683 |
| 7204 | Itsuwaribito Ushiho | 3571 |
| 7233 | Yakuza Girl | 74223 |
| 7276 | Fushou no Musuko | 13632 |
| 7282 | Horror Collector | 15851 |
| 7295 | Koko | 4428 |
| 7299 | Medaka-Box | 24594 |
| 7303 | La méthode pour dessiner les mangas | 16502 |
| 7331 | Le garçon de la lune | 10959 |
| 7332 | Mabinogi | 18926 |
| 7358 | YELLOW | 1682, 31888 |
| 7406 | Dice | 2038, 35879 |
| 7407 | Dokuro | 23468 |
| 7499 | Kazuhiro Fujita's short stories | 36529 |
| 7501 | Kasane | 21192 |
| 7521 | Fortune Arterial | 13620 |
| 7556 | Silent love | 2890 |
| 7576 | Sky wars | 13113 |
| 7668 | Oh my God ! | 5592, 7437, 32033 |
| 7678 | Gurren Lagann | 3514 |
| 7703 | Jiya | 6529, 26618 |
| 7704 | Sachie-chan Guu!! | 26618 |
| 7710 | Pardonne moi | 21929 |
| 7754 | La Bible Manga | 5261 |
| 7829 | Subaru 2 - Moon | 14337, 25955 |
| 7844 | Get Love !! | 15819 |
| 7883 | Durarara!! | 25151 |
| 7894 | Gate | 14458, 21620 |
| 7948 | The Legend of Koizumi | 21297 |
| 8007 | My Lovely Ghost Kana | 2819 |
| 8009 | Full Contact | 16724 |
| 8015 | Strawberry Panic | 6513 |
| 8062 | Koisuru Usotsuki | 18987 |
| 8070 | Guin Saga : Les Sept Mages | 23444 |
| 8087 | Ghost in The Shell - Stand Alone Complex | 3820 |
| 8109 | Family | 5358, 8819, 17819 |
| 8135 | Momo - La Petite Diablesse | 13672 |
| 8163 | Ga-Rei Tsuina no Shou | 22350 |
| 8169 | Dorothy | 11057 |
| 8192 | Puipui ! | 20414 |
| 8214 | Love Tic | 1911 |
| 8224 | Anastasia Club | 13054 |
| 8279 | Honnori Vanilla | 13339 |
| 8280 | Tau | 8728 |
| 8298 | Yukkuri Koi wo Shiyou | 23747 |
| 8305 | Bokura no Oukoku Arabian Night | 21301 |
| 8307 | Kooru Shakunetsu | 3064 |
| 8308 | Homura no Kusari | 3064 |
| 8309 | Mitsu no Rakuin | 3064 |
| 8310 | Rekka no Shizuku | 3064 |
| 8333 | Perfect World | 5098, 21962, 26938 |
| 8388 | Kawaii Hito - Eiiri MISONO | 2936 |
| 8450 | Kokoro No Kagi Wo Mitsukete | 4660, 21022 |
| 8495 | RIN | 2357, 3663, 7891 |
| 8516 | Itoshi no Kana | 2819 |
| 8646 | Gosick | 13508 |
| 8648 | Mobile Suit Gundam Uc | 6015 |
| 8691 | Rakuda ga Warau -Final Cut- | 21890 |
| 8720 | Lock-on! | 6158 |
| 8733 | Sabu et Ichi | 22770 |
| 8815 | I Love You | 142, 3972, 10394, 13303, 13993, 14078, 16446, 20079, 20138, 20322, 20712, 24283, 36965, 40738, 42668 |
| 8817 | Jumbor Angzengbang | 3183 |
| 8827 | Sengoku Basara - Roar of Dragon | 6115 |
| 8861 | Lily la menteuse | 7164 |
| 8898 | Ethnicity 01 | 12858 |
| 9139 | Kinnikuman nisei | 11529 |
| 9172 | Zen Zen | 15109 |
| 9192 | Omo ni Naitemasu | 15075 |
| 9273 | Akumugari | 26415 |
| 9279 | Happy Pretty | 9957 |
| 9352 | Le Capital | 23249 |
| 9354 | ZERO | 2686, 8579, 9254, 10516, 25518 |
| 9422 | Dr. Mordrid | 40948 |
| 9493 | Gui | 715, 21698, 22200 |
| 9494 | The Swordsman | 20321 |
| 9496 | Sugar Dark | 17576, 34100 |
| 9529 | Hotel | 4993, 36740 |
| 9534 | Dear ! | 7672, 9810, 32333 |
| 9545 | Bamboo Blade B | 22473 |
| 9557 | Ryoma | 5485 |
| 9582 | Gallop | 8880 |
| 9634 | Tokyo Ravens | 16052 |
| 9776 | Full Moon (Shiozawa) | 16944, 20818, 23180 |
| 9849 | J.boy | 6377 |
| 9894 | Prince | 24793 |
| 9899 | La Fille Perverse [Junji Ito Collection n°11] | 63608 |
| 9954 | Kurogane Communication | 6859 |
| 10004 | The Innocent | 18075, 25489 |
| 10058 | Cardfight!! Vanguard | 22440 |
| 10086 | Yamato Le Cuirassé de l'Espace | 7190, 7194, 7198 |
| 10139 | Cat Life | 7051 |
| 10237 | Accomplice | 4131 |
| 10360 | Boukyaku Cradle | 7819 |
| 10361 | Enemigo | 9091 |
| 10403 | Iris Zero | 12718 |
| 10422 | Happy boy | 16515 |
| 10437 | Shin devilman | 24475 |
| 10501 | Joker | 14199, 29954 |
| 10512 | A la Recherche du Temps Perdu | 47827 |
| 10548 | Pokemon RéBURST | 17276 |
| 10594 | Half Prince | 17159 |
| 10708 | Bloody Mary | 3550, 25588, 55290 |
| 10731 | Ore no Imôto ga Konna ni Kawaii Wake ga Nai | 714, 11730 |
| 10749 | Shin Mazinger Zero | 24820 |
| 10774 | Takahashi Rumiko gekijou | 27678 |
| 10785 | Negative | 38463 |
| 10794 | Yotsunoha | 24416 |
| 10795 | Youko x Boku SS | 4294 |
| 10804 | Alien 9 - The Emulators | 1937 |
| 10861 | Liselotte et la forêt des sorcières | 18619 |
| 10911 | N°6 | 16579 |
| 10948 | Another | 16161 |
| 10949 | Fate/Zero | 18251 |
| 10975 | Maoyû Maô Yûsha - Kono Watashi no Mono Tonare, Yûsha yo - Kotowaru | 19918 |
| 11027 | Secret'R Heure Sup' | 11313 |
| 11067 | Lion Heart | 71775 |
| 11077 | Notre royaume - Mille et Une Nuits | 21301 |
| 11088 | Furari | 4794 |
| 11138 | Lion | 5877 |
| 11231 | A.D Angel's Doubt | 3296 |
| 11316 | QUIZ | 6167, 34080 |
| 11317 | SNOW BLIND | 4834, 10118 |
| 11424 | Cimoc | 16521 |
| 11430 | Engage | 7110 |
| 11459 | Trigger - TAKEMURA Yuji | 83, 59661 |
| 11462 | Ore no Kanojo to Osananajimi ga Shuraba Sugiru | 20115 |
| 11484 | Sin | 65271 |
| 11574 | Boku wa tomodachi ga sukunai | 7125 |
| 11608 | Border | 10711, 28319, 34957 |
| 11609 | Mezase Hero ! | 17066 |
| 11669 | Yamato | 30550 |
| 11697 | Mahoromi - Chroniques architecturales de l'espace-temps | 15910 |
| 11760 | Chopperman | 11054, 14631 |
| 11766 | Secret'R en RTT | 11313 |
| 11798 | Kaerenai Futari | 27014 |
| 11800 | The Mystic Archives of Dantalian | 12996 |
| 11881 | Blue | 3105, 5216, 13817, 15209, 18345, 18488 |
| 11900 | The Earl and the Fairy | 21652 |
| 11936 | Liar x Liar | 17286, 59102 |
| 12025 | Lovely Complex Two | 19182 |
| 12073 | End of The World | 72238 |
| 12160 | Dix-huit et Vingt ans | 18504 |
| 12170 | Amai Suppai Horonigai | 11599 |
| 12249 | Rozen Maiden - Dolls Talk | 2609 |
| 12286 | Undertaker Riddle | 2626 |
| 12312 | Ore no Kôhai ga Konna ni Kawaii Wake ga Nai | 714, 19475 |
| 12341 | Nobody Knows | 22850 |
| 12353 | C- | 34665 |
| 12492 | Seigi no Mikata | 3861, 20873 |
| 12512 | Virtus | 4590, 52402 |
| 12552 | Boy Meets Girl - Mound no Shôjo | 25612, 37316 |
| 12600 | Le Prince | 24793 |
| 12692 | Seitokai no Ichizon | 498 |
| 12743 | Dark Rabbit | 3615 |
| 12814 | Disu Ma Topia | 5327 |
| 12839 | Accel World | 14280, 21761 |
| 12859 | Maria | 5299, 5303, 34051 |
| 12912 | Arsène Lupin | 16500 |
| 12915 | Rin! - TAKEUCHI Ayaka | 2357, 3663, 7891 |
| 12926 | Enfer bleu | 1992, 9782 |
| 12976 | Ultraman | 6257, 55582 |
| 13055 | Lulu to Mimi | 18697 |
| 13059 | Le Tunnel [Junji Ito Collection n°13] | 24366, 63608, 75803 |
| 13066 | G | 13033, 76808 |
| 13069 | Mawang Le Roi des Démons | 77210 |
| 13109 | Gurazeni | 19548 |
| 13111 | Storm Lover Natsukoi!! | 15430 |
| 13112 | Storm Lover | 15430 |
| 13131 | Lovely Complex Two | 19182 |
| 13203 | Leo | 66077 |
| 13204 | Badminton Girl | 17296, 62691 |
| 13230 | Montage | 6648 |
| 13234 | Victory Kickoff !! | 16419 |
| 13254 | Shinsekai Yori | 21466 |
| 13365 | Natsuiro Kiseki | 24117 |
| 13392 | Mahôka Kôkô no Rettôsei - Kyûkôsen hen | 25306 |
| 13444 | Amai Suppai Horonigai | 11599 |
| 13464 | Kerberos | 3816 |
| 13539 | Baby Doll | 34102 |
| 13583 | Climber | 15190 |
| 13644 | Faust | 13024, 34969 |
| 13666 | Laughing Under the Clouds | 19616 |
| 13692 | How Good was I ? | 26409 |
| 13722 | My Bodyguard | 5116 |
| 13743 | Etc. | 20476 |
| 13745 | Eulen Spiegel | 10892, 10894 |
| 13771 | Hori-san to Miyamura-kun Omake | 23958 |
| 13774 | Aquarion Evol | 18300 |
| 13787 | Nobody Knows | 22850 |
| 13879 | Jeux d'enfants | 3809, 5898, 16994, 21466, 25350 |
| 13903 | IPPO | 15732 |
| 13933 | Le journal de Hanako | 422, 8139, 13277 |
| 13967 | Steins;Gate - Aishin Meizu no Babel | 5268 |
| 13980 | Rozen Maiden - Dolls Talk | 2609 |
| 14022 | Twin | 23258 |
| 14037 | Hidan no Aria | 4925 |
| 14101 | Black Bard | 22356 |
| 14207 | Kawaii Hito | 2936, 4185, 37922, 57106, 74099 |
| 14266 | Fool's paradise | 31032 |
| 14332 | Kizuato | 15376 |
| 14335 | Koharu Biyori | 13987, 15066, 39507 |
| 14342 | Koigokoro | 2582, 7008, 22492, 26908 |
| 14346 | Konohanatei Kitan | 16817 |
| 14355 | Ultrabaroque Deprogrammer | 14820 |
| 14415 | Ai no Katachi | 4930 |
| 14457 | Devil King | 21906 |
| 14584 | All nude | 10118 |
| 14594 | Slow sex | 2835 |
| 14622 | Le mort amoureux [Junji Ito Collection n°14] | 63608 |
| 14728 | Zenki | 12264 |
| 14909 | Warlord | 13543 |
| 14965 | Ai | 3436, 19567 |
| 14969 | Ultraman | 6257, 55582 |
| 15006 | Letort | 8109 |
| 15028 | You're My Only Shinin' Star | 15260 |
| 15039 | Iczer densetsu | 7168 |
| 15067 | Daisuki | 8089, 34548 |
| 15080 | Graffiti | 10630 |
| 15200 | Ginga he kick-off !! (1ère série) | 16419 |
| 15216 | Kuroneko – Le jeu | 711 |
| 15254 | Man-ken | 180 |
| 15276 | Chocolate & Tan | 501 |
| 15287 | Border | 10711, 28319, 34957 |
| 15291 | Date A Live | 15650 |
| 15308 | nobody knows… | 22850 |
| 15325 | Campione ! | 20202 |
| 15508 | Ephemeral | 42716 |
| 15522 | Femme fatale | 55164 |
| 15546 | Outbreak Company | 26211 |
| 15586 | Log Horizon | 1762 |
| 15588 | Strike The Blood | 20722 |
| 15609 | Ore no Nounai Sentakushi ga, Gakuen Love Come o Zenryoku de Jama Shiteiru | 13309 |
| 15610 | NaNa | 74, 24232, 32745 |
| 15806 | Someday | 39746 |
| 15816 | Hatsukoi monogatari | 24145, 54321 |
| 15901 | STONe | 1220 |
| 15915 | Adult Privilege | 1114 |
| 36893 | NightS | 2196 |
| 36923 | Nepenthès | 37891 |
| 36927 | Katsuraakira | 26618 |
| 37472 | No Game No Life | 5923 |
| 37553 | Black Bullet | 1179 |
| 37661 | Cagaster | 54195 |
| 38060 | Dear Brother ! | 3038, 18244, 23458 |
| 38066 | My sister + | 8641, 27086 |
| 38207 | The Heroic Legend of Arslân | 9356, 9360 |
| 38284 | Inu & Neko | 2360, 9413, 35420 |
| 38421 | Mako : l'ange de la mort | 27216 |
| 38469 | cue - Hatsukoi tanpenshû | 14782 |
| 38549 | The world revolves around you | 14221 |
| 38568 | Last exile - Fam aux ailes d'argent | 21053 |
| 38609 | Maou Lover | 71019 |
| 38898 | RIN ! | 2357, 3663, 7891 |
| 39011 | Tsuide ni tonchinkan | 39822 |
| 39279 | Le Coeur de la méprise | 3047, 26331, 37652 |
| 39434 | The Garden of Words | 10158 |
| 39441 | Mon jeune chat et mon vieux chien | 23960 |
| 39614 | Kuro, un coeur de chat | 5644 |
| 40069 | Citrus | 2123, 7727, 54577 |
| 40264 | Kuroneko - La dépendance | 10574 |
| 40308 | Burning hell | 3375 |
| 40394 | Pokémon XY | 33447 |
| 41015 | Dilemma | 30132 |
| 41049 | Die & Retry | 26598 |
| 41307 | The testament of sister new devil | 10169 |
| 41569 | Etotama | 33331 |
| 41603 | Heidi (Classiques en manga) | 34837 |
| 41607 | Border | 10711, 28319, 34957 |
| 41738 | DRAMAtical Murder | 23757 |
| 41739 | Love & Peach | 73887 |
| 41741 | Sword Art Online - Progressive | 10924 |
| 41811 | Dans un coin de ciel nocturne | 10863 |
| 41907 | Maou lover vs le prince | 71019 |
| 42108 | DanMachi - La Légende des Familias | 17189 |
| 42182 | Re:Monster | 25426 |
| 42208 | Alice au Pays des Merveilles (classiques en manga) | 25122 |
| 42246 | Snow drop | 1211, 25220 |
| 42394 | Mushoku Tensei | 25541 |
| 42950 | Kuroneko – La passion | 25578 |
| 43711 | Père & fils | 10565 |
| 43894 | Tsuiteru Kanojo. | 423, 10965, 25953 |
| 44206 | Petshop of Horrors Passage-Hen | 56896 |
| 44452 | Mazinger Z | 17791 |
| 44456 | Tamayura | 22637 |
| 44556 | Ore ga Ojou-sama Gakkou ni Shomin Sample Toshite Rachirareta Ken | 2642 |
| 44696 | The New Gate | 26970 |
| 44712 | Moi, quand je me réincarne en slime | 35483 |
| 44713 | Overlord | 26616 |
| 44744 | Les Héros de la Galaxie | 22118, 36534 |
| 44868 | The Story of Never Ending Unhappiness | 26331 |
| 44870 | Hunt - Le Jeu du Loup-Garou | 26400 |
| 44880 | Kuroneko – La tourmente | 26965 |
| 44895 | Nietzsche-sensei - Konbini ni, Satori Sedai no Shinjin ga Maiorita | 26818 |
| 45079 | Tomogui | 39798 |
| 45413 | Yu-Gi-Oh! Arc-V | 36598 |
| 45419 | The Rising of the Shield Hero | 25524, 56853 |
| 45616 | Stranger Case | 35849 |
| 46381 | Le bateau-usine | 34970 |
| 46383 | Kanikousen | 34970 |
| 46716 | Fairy tail - Side stories | 37805 |
| 46904 | FATE/APOCRYPHA | 38064 |
| 47112 | Your name. | 7310, 14577, 38482 |
| 47307 | À la folie... pas du tout ! | 60317 |
| 47417 | All Out!! | 25731 |
| 47548 | My Teen Romantic Comedy is wrong as I expected | 27003 |
| 47627 | Roku de Nashi Majutsu Koushi to Kinki Kyouten | 36656 |
| 47912 | Le magicien d'Oz (classiques en manga) | 55737 |
| 48146 | Neo Parasite | 26687 |
| 48155 | Madan no ô to Vanadis | 23429 |
| 48232 | Chingyo | 70753 |
| 48365 | Anne et la Maison aux Pignons Verts | 19952 |
| 48589 | Pygmalion | 15339, 37836 |
| 48722 | Im | 35090 |
| 49029 | Okujou no Yurirei-san | 35425 |
| 49031 | Despicable | 53979 |
| 49192 | Goblin Slayer | 37781 |
| 49244 | How NOT to Summon a Demon Lord | 36501 |
| 49385 | Classroom for heroes | 27719, 38923 |
| 49388 | Dresseuses de monstres | 40991 |
| 49586 | O.B. | 19343 |
| 49597 | Alderamin on the sky | 27585 |
| 49666 | Tsuki ga Michibiku Isekai Douchuu | 36470 |
| 50131 | Merveilleuse Creamy | 19448 |
| 50243 | So I'm a spider, so what ? | 37173 |
| 50278 | My brother | 3881 |
| 50500 | Konosuba - Sois Béni Monde Merveilleux | 26785 |
| 50608 | Pas touche au petit chat ! | 37664 |
| 50769 | Chocolate Vampire | 39451 |
| 50779 | Kodai Ouja Kyouryuu King | 10486 |
| 50889 | Death March kara Hajimaru Isekai Kyousoukyoku | 26906 |
| 51515 | Chocotan | 501 |
| 51824 | 25 histoires d'un monde en 4 dimensions | 8883 |
| 51870 | Grimoire of Zero | 34083 |
| 52069 | Hunt - Beast Side | 40554 |
| 52166 | Hero Skill : Achats en ligne | 39104 |
| 52302 | Canis -The Speaker- | 18224 |
| 52372 | Fate/Grand Order -mortalis:stella | 40924 |
| 52494 | Isekai Mahou wa Okureteru | 38895 |
| 52497 | Magical Dance | 3484 |
| 52588 | Malédiction finale | 36782 |
| 52643 | Lost Children | 5434 |
| 52694 | Coyote | 38891 |
| 52853 | Les talons Aiguilles Rouges | 37983 |
| 52911 | The irregular at magic high school | 50190 |
| 52918 | Skeleton Knight in Another World | 39044 |
| 52919 | Beyond the Clouds | 14936 |
| 52991 | Le Fantôme de l'Opéra | 22795 |
| 53013 | Atomic (s)trip | 68935 |
| 53080 | Shikkaku Mon no Saikyou Kenja - Sekai Saikyou no Kenja ga Sara ni Tsuyokunaru Tame ni Tensei Shimashita | 39834 |
| 53144 | Le Huitième fils | 36603 |
| 53183 | Fatale fiancée | 34967 |
| 53397 | It can't be helped | 1113 |
| 53401 | Harem in the Fantasy World Dungeon | 39725 |
| 53562 | Boku no danna-sama | 37598, 56777 |
| 53563 | Hinata | 15137 |
| 54021 | Noise | 1052, 52903 |
| 54206 | Trace | 3529, 78595 |
| 54207 | Jackass! | 37749 |
| 54228 | La Sorcière Invincible | 40717 |
| 54381 | Lord El-Melloi II-sei no Jikenbo | 49662 |
| 54498 | Kirishima, bukatsu yamerutte yo | 25643 |
| 54539 | Les Carnets de L'Apothicaire | 39631 |
| 54683 | Orient - Samurai quest | 40964 |
| 54775 | Princesse détective | 23410 |
| 54785 | Life | 919, 14449 |
| 54852 | Réincarné dans un autre monde | 54215 |
| 55143 | Train de nuit dans la voie lactée | 21035, 21039, 21042, 40559 |
| 55245 | Gift | 27113, 36506 |
| 55863 | La métamorphose | 13632, 25710, 26867 |
| 55873 | TODAG - Tales of demons and gods | 37605 |
| 55887 | Rougo ni sonaete i sekai de 8 man-mai no kinka o tamemasu | 40881 |
| 56116 | Remake | 67773 |
| 56133 | Kaifuku Jutsushi no Yarinaoshi | 40299 |
| 56510 | Zozo Zombie | 47344 |
| 56574 | Danshi Koukousei wo Yashinaitai Onee-san no Hanashi. | 40819 |
| 56581 | 50 nuances de gras | 38969 |
| 56595 | Noble new world adventures | 54063 |
| 56726 | from End | 39758 |
| 56931 | Mao | 54585 |
| 57133 | Samurai 8 | 54443 |
| 57164 | Le Capital | 23249 |
| 57166 | Crime et Châtiment | 92, 3461, 5982, 39944 |
| 57209 | Ore dake Haireru Kakushi Dungeon: Kossori Kitaete Sekai Saikyou | 54085 |
| 57211 | Level 1 dakedo Unique Skill de Saikyou desu | 54682 |
| 57267 | Boukensha ni Naritai to Miyako ni Deteitta Musume ga S Rank ni Natteta | 41097 |
| 57391 | Dai Dark | 54000 |
| 57419 | X-Day | 1039, 1310 |
| 57428 | Utakata ni Warau | 19616 |
| 57444 | Tomoe Ga Yuku! | 11548 |
| 58027 | Itsuwaribito | 3571 |
| 58169 | Hypnosis Mic -Division Rap Battle- side B.B & M.T.C | 57753, 64426 |
| 58241 | A Sweet Beast | 19140 |
| 58357 | The Yakuza's guide to babysitting | 54313 |
| 58384 | The Ice Guy & The Cool Girl | 55151 |
| 58454 | Only you | 24926 |
| 58528 | Hada Camera | 40194 |
| 58606 | Atarashii Joushi wa Do Tennen | 67203 |
| 58609 | Furare Girl | 56298 |
| 58639 | A 25:00, à Akasaka | 77624 |
| 59169 | Mythical Beast Investigator | 40263 |
| 59187 | Ashon de yo - Uchi no Inu Log | 37817 |
| 59352 | Back to you | 56345, 74880 |
| 59510 | Chastity reverse world | 38920 |
| 59699 | Citrus+ | 2123, 7727, 54577 |
| 59812 | Switch Me On | 57136 |
| 59824 | Climax | 13710 |
| 59974 | Tokyo Shinobi Squad | 54747 |
| 60210 | Fake | 725, 5016, 11088, 12545 |
| 60217 | Rascal Does Not Dream of Bunny Girl Senpai | 37286 |
| 60456 | L'Amant | 8950 |
| 60458 | HigeHiro | 54191 |
| 60507 | Plongée dans la nuit | 56367 |
| 60627 | Chillin' Life in a Different World | 54844 |
| 60642 | Les fleurs de la mer Égée | 58585 |
| 60857 | Arafoo Kenja no Isekai Seikatsu Nikki | 54029, 73484 |
| 60873 | Hitman, Les coulisses du manga | 16301 |
| 60949 | Napoléon | 19338 |
| 61022 | Shaman King Marcos | 71151 |
| 61936 | Les Enfants du Temps | 55885 |
| 61941 | Cautious hero | 54148 |
| 61971 | Ayakashi Triangle | 56510 |
| 62029 | La Forêt aux Lapins | 57075 |
| 62055 | Teach Me More | 6342, 17420 |
| 62109 | Le Renard et le Petit Tanuki | 63744 |
| 62148 | Kase-san & Yamada | 39512 |
| 62390 | Kitsune no Oyome-chan | 53984 |
| 62492 | Ce que veut dieu ! | 16994 |
| 62683 | My Happy Marriage | 57874 |
| 62833 | A Safe New World | 55125 |
| 62946 | Kizoku Tensei: Megumareta Umare kara Saikyou no Chikara wo Eru | 57593 |
| 62985 | En garde ! | 63022, 70779 |
| 63155 | Un oisillon sur le rivage | 53778 |
| 63158 | La vie en rose | 651, 10213, 15337, 49964 |
| 63160 | À tes côtés... | 57580 |
| 63304 | La Gameuse et son Chat | 55061 |
| 63320 | Wild Police Story | 60741 |
| 63391 | Terrarium | 57569 |
| 63594 | Handyman Saitou in Another World | 54166 |
| 63690 | Kamen Rider V3 & X | 78703 |
| 64326 | Triangle | 19631 |
| 64357 | Inakunare, Gunjou Fragile Light of Pistol Star | 40873 |
| 64423 | Kuma Kuma Kuma Bear | 40840 |
| 64478 | Les Fées, Le Roi-Dragon et Moi (En chat) | 40495 |
| 64607 | The Most Notorious Talker | 59842 |
| 64725 | Double | 1925, 16218, 65657 |
| 64782 | Reincarnated as a Sword | 39113 |
| 64972 | Leviathan | 55656, 64459 |
| 64990 | Mon chat à tout faire est encore tout déprimé | 62015 |
| 65046 | Wandering witch | 54318 |
| 65314 | Teenage Renaissance | 54049 |
| 65365 | Escape | 2146, 23974 |
| 65398 | La sérénade du corbeau | 65918 |
| 65458 | Notre Paradis | 1123, 23935 |
| 65459 | Flow | 39816, 40939 |
| 65511 | Share | 61316 |
| 65516 | Adachi et Shimamura | 37929, 55490 |
| 65553 | Magica | 73155 |
| 65655 | Goodbye santa claus | 8752 |
| 65763 | The World's Finest Assassin Gets Reincarnated in Another World as an Aristocrat | 54567 |
| 65814 | Badass Cop & Dolphin | 56702 |
| 65827 | Eizouken ni wa Te wo Dasu na! | 53427 |
| 65871 | In the Land of Leadale | 57806 |
| 65876 | I'm in Love with the Villainess | 57391 |
| 65878 | The Eminence in Shadow | 54238 |
| 65916 | Failure Frame | 56077 |
| 65942 | Re:naissance | 58243 |
| 65957 | La bête du roi | 56288 |
| 66121 | 5-nin no Ou | 49799 |
| 66181 | BEM | 60656 |
| 66218 | Super Cub | 54241 |
| 66303 | Birth | 323, 23814 |
| 66334 | He Came for Learning "Love" | 4868 |
| 66350 | Saiyuki | 1162 |
| 66378 | Adabana | 58586 |
| 66441 | I want you | 7222 |
| 66469 | Rooster Fighter - Coq de Baston | 58454 |
| 66705 | The Most Heretical Last Boss Queen | 57219 |
| 66786 | My Gift LVL 9999 Unlimited Gacha | 60003 |
| 66869 | Le prince | 24793 |
| 67031 | Demon Lord, Retry ! | 40761 |
| 67032 | Demon Lord, Retry ! R | 57444 |
| 67042 | La couleur de l'eau | 1235 |
| 67043 | Shin Samurai Troopers - Le réveil des samouraïs de l'éternel | 6132 |
| 67245 | Heaven's Design Team | 40981 |
| 67252 | Yotsukoto | 41234 |
| 67263 | Kottou Nekoya | 40242 |
| 67323 | Écoute ton coeur, Atami ! | 76043 |
| 67366 | Si je suis la Vilaine, autant mater le boss final | 54428 |
| 67367 | Archdemon's Dilemma | 41267 |
| 67391 | Polar Night | 67195 |
| 67424 | Que reste-t-il de nos rêves ? | 61721 |
| 67628 | Loin de moi, près de toi | 67044 |
| 67659 | Haru ga Kita | 40766 |
| 67921 | Dandara | 21857 |
| 67928 | Anne... la maison aux pignons verts | 19952 |
| 68098 | Mikata ga Yowasugite Hojo Mahou ni Tesshiteita Kyuutei Mahoushi, Tsuihou Sarete Saikyou wo Mezashimasu | 65046 |
| 68156 | Lovely | 11109 |
| 68238 | Babel | 23047, 37895 |
| 68279 | Tadokoro-san | 55171 |
| 68308 | Brutal: Satsujin Kansatsukan no Kokuhaku | 56533 |
| 68402 | Koinegau Horizonte | 52138 |
| 68407 | Lost Lad London | 61360 |
| 68539 | Meurtres dans le décagone | 60650 |
| 68569 | Mix | 16694 |
| 68573 | BLT | 31798 |
| 68579 | Though I Am an Inept Villainess | 69479 |
| 68598 | Playback | 7045 |
| 68614 | Kimi wa Tomodachi | 63201 |
| 68672 | Rebuild the World | 55194 |
| 68685 | Kozure Ookami | 1973, 24382 |
| 68724 | Yuujin ga Yuusha | 20217 |
| 68727 | Kaijû Girl Carameliser | 52956 |
| 68728 | Bara Kangoku no Kemono-tachi | 38764 |
| 68842 | Kimi to Koete Koi ni Naru | 62833 |
| 68844 | Kono Te wo Hanasanai de | 69722 |
| 69058 | Magic Maker | 65235 |
| 69122 | How a Realist Hero Rebuilt the Kingdom | 39754 |
| 69267 | Cette vie auprès de toi | 54461 |
| 69385 | Fundari, Kettari, Aishitari | 69217 |
| 69389 | Hallelujah Baby | 61608 |
| 69518 | Getter Robo High | 53687 |
| 69562 | Skip & Loafer | 54780 |
| 69701 | Hotel | 4993, 36740 |
| 69714 | Gestalt | 3655 |
| 69718 | Under Ninja | 41259 |
| 69741 | Villainess Level 99 | 56186 |
| 69756 | Reign of the seven Spellblades | 55864 |
| 69867 | Partners 2.0 | 57795 |
| 70017 | Gogatsu no Hana wa Mada Sakanai | 52602 |
| 70037 | Ookami-kun Won't Let Go | 56766 |
| 70038 | Thoroughbred wa Yuruganai | 57566 |
| 70043 | More than Words | 20771 |
| 70058 | La dresseuse sans étoiles parcourt le monde (pour récolter des déchets) | 57562 |
| 70119 | Tengu hunter brothers | 70010 |
| 70140 | Crazy Food Truck | 57351 |
| 70160 | My Beautiful Boy | 6208 |
| 70257 | Il ne comprend pas qu'il me plaît | 57906 |
| 70277 | Nanaka 6/17 + | 8966 |
| 70319 | Meguro & Akino | 59278 |
| 70368 | I'm sorry | 7484 |
| 70424 | Spice and Wolf - Wolf & Parchment | 59499 |
| 70515 | The Reincarnation of the Strongest Exorcist in Another World | 57916 |
| 70625 | Lethal Experiment | 60429 |
| 70633 | Short Peace | 9050 |
| 70640 | Seihantai na Kimi to Boku | 63680 |
| 70642 | Ibitte Konai Gibo to Gishi | 64711 |
| 70677 | Hachi & Maruru - Chats des rues | 63845 |
| 70809 | Conqueror Of The Dying Kingdom | 68113 |
| 70811 | Power Antoinette | 63057 |
| 70817 | Haru no Arashi to Monster | 64245 |
| 70893 | step by step Sara | 68118 |
| 70915 | Eizôken ! Pas touche à nos animés !! | 53427 |
| 71075 | L'habitant de l'infini - Bakumatsu | 77874 |
| 71077 | Summer Ghost | 63013 |
| 71139 | Pink Ribbon | 61713 |
| 71144 | Villageois LVL 999 | 39720 |
| 71150 | Rajou Koidzukiyo | 65917 |
| 71169 | Ça passe ou ça casse ! | 57717, 76032 |
| 71212 | Hikyouiku kara Nigetai Watashi | 57218 |
| 71240 | A Rank Party wo Ridatsu Shita Ore wa, Moto Oshiego Tachi to Meikyuu Shinbu wo Mezasu | 61295 |
| 71287 | The One Within the Villainess | 62295 |
| 71438 | Samouraï Pizza Cats | 7200 |
| 71505 | Reborn As A Vending Machine | 61284 |
| 71514 | Ashigei Shoujo Komura-san | 54272 |
| 71516 | A-Girl | 2740 |
| 71680 | La règle de trois | 74484 |
| 71748 | Miyabichi no Onmyôji - L'Exorciste hérétique | 70425 |
| 71801 | Mizuno et Chayama | 60881 |
| 72116 | De neiges et de flammes | 35435 |
| 72225 | I cannot reach you | 58902 |
| 72312 | Monsieur Méchant va détruire la terre (après ses congés) | 54257 |
| 72353 | Classroom of the Elite | 37533 |
| 72360 | Babel - The New Hakkenden | 23047, 37895 |
| 72386 | Hapipuri - Happy na Pretty-chan | 9957 |
| 72417 | HALF MOON | 2233 |
| 72485 | C'est une belle journée pour un labyrinthe ! | 72862 |
| 72501 | Qu'est-ce qui cloche avec ta vie en ligne ? | 55435 |
| 72640 | The Do-Over Damsel Conquers the Dragon Emperor | 57758 |
| 72641 | I Can’t Believe I Slept With You! | 55928 |
| 72656 | Eisen Flügel | 36517 |
| 72668 | Private lesson | 4667, 34680 |
| 72701 | Le Septième Prince | 58003 |
| 72959 | Days with my stepsister | 60504 |
| 73034 | Tank Chair | 66888 |
| 73042 | Red Blue | 75923 |
| 73104 | Pink Heart Jam beat | 77691 |
| 73335 | Le guérisseur de l'ombre | 69286 |
| 73513 | Taxi | 58149 |
| 73591 | Le Comte de Monte-Cristo | 9753 |
| 73676 | Tomozaki-kun est un loser ! | 40442 |
| 73679 | Magical revolution - La princesse réincarnée et la jeune prodige | 58411 |
| 73688 | Love me tender | 32745 |
| 73697 | Internet Love! | 7146 |
| 73745 | Reincarnated Into a Game as the Hero's Friend | 66584 |
| 73751 | Bye bye Earth | 71437 |
| 73808 | Swingin' Dragon & Tiger Boogie | 60100 |
| 73832 | Petit Requin | 67256 |
| 73872 | Welcome to Japan! Elfe de mes rêves... | 54506 |
| 74176 | Tsukimichi - Moonlit fantasy | 36470 |
| 74559 | La vengeance d'Odin | 73391 |
| 74679 | Une fille si féline | 70721 |
| 74854 | Planetarium Ghost Travel | 75925 |
| 75021 | Coup de foudre dans ta face ! | 54207 |
| 75177 | Dead Account | 65937 |
| 75312 | 40 Made ni Shitai 10 no Koto | 69042 |
| 75529 | De neige et d'encre | 39812, 62924, 68069 |
| 75804 | Nos premiers pas | 7337 |
| 75854 | Fuyu monogatari | 11514 |
| 76110 | Virgin Knight: I Became the Frontier Lord in a World Ruled by Women | 67064 |
| 76114 | No-Go Zone Level X | 72948 |
| 76280 | Young Ladies Don't Play Fighting Games | 56724 |
| 76431 | Teacher in the Destruction Classroom | 73308 |
| 76509 | X-Men - Le manga | 459 |
| 76537 | I love you | 142, 3972, 8004, 10394, 13303, 13993, 14078, 16446, 20079, 20138, 20322, 20712, 24283, 36965, 40738, 42668 |
| 76557 | Silent Witch | 61432 |
| 76926 | Cher époux inconnu, je veux divorcer | 73781 |
| 77075 | I Parry Everything | 58341 |
| 77427 | Double suicide | 68557 |
| 77449 | Akari | 72579 |
| 77468 | Ne lâche pas ma main | 69722 |
| 77491 | Tani & Suzuki - You and I are polar opposites | 63680 |
| 77492 | Menya | 65582 |
| 77514 | Your forma | 61358 |
| 77526 | Yano - Une vie ordinaire | 66892 |
| 77659 | Le Harem De L'Impératrice | 59126 |
| 77821 | Smother me | 73174 |
| 77859 | The Regalia of the Underdog | 75885 |
| 78024 | The strange pictures | 76353 |
| 78073 | Rairairaise | 77038 |
| 78146 | Witch and mercenary | 71101 |
| 78306 | Par-delà les neiges éternelles | 67781 |
| 78410 | Love Bullet | 7463, 70972 |
| 78411 | Zange | 70936 |
| 78498 | Spring Storm and Monster | 64245 |
| 78503 | Polaris of dawn | 65430 |
| 78507 | Underground | 5056, 18237 |
| 78510 | Cats and Dragon | 75228 |
| 78557 | Les Fiancés de l'ère Taisho | 77422 |
| 78566 | Nova et la foret des monstres | 78154 |
| 78636 | Goze Hotaru | 71546 |
| 78742 | Shiba Inu Rooms | 72264 |
| 78815 | Blade & Bastard | 67505 |
| 79121 | Kiss me Kitty | 58145 |
| 79193 | Mad | 19966, 72171 |
| 79269 | Inkya Gal Revolution! | 65330 |
| 79270 | Now That We Draw | 66841 |
| 79289 | Hotel Inhumans | 60562 |
| 79290 | Oversleeping Takahashi | 72477 |
| 79291 | Sentenced to be a hero | 67527 |
| 79357 | Le bouquiniste | 78706 |
| 79498 | Mermaid Prince | 8712 |
| 79697 | Star - Strike it Rich | 66882 |
| 79743 | Himaten! | 72381 |
| 79763 | L'amour de Belladonna | 32745, 33746 |
| 79770 | Magical Girl Dandelion | 72806 |
| 79804 | Saint Seiya Rerise of Poseidon | 67558 |
