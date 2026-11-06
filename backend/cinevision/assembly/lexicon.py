"""Story-cue words in 15 languages, so a description written in Telugu, Hindi, Japanese… is matched to clips
without any AI. English keeps its own regular expressions in engine.CUES; this table adds the other languages.

Each cue (night, outside, crowd, run…) lists common words per language. The reverse map also includes every
language's translated cue label from director/i18n/<lang>.py, minus a few labels that are too ambiguous as
search words (EXCLUDE).

How words are matched (no AI, deterministic, labelled as an estimate by the engine):
* Japanese / Chinese (no spaces): substring search, longest word first, so 夜明け (dawn) is not also read as 夜 (night).
* Indian scripts, Korean, Arabic (words take endings): a word matches the start of a token when it is at least
  4 characters (Korean: 2 syllables, Arabic: 3 letters); shorter words must match a whole token. Arabic tokens are
  also tried without the common prefixes و ب ف ل ك ال.
* Latin scripts: whole words; a trailing * marks a stem (noche* matches noches).
* Several words separated by a space form a phrase that must appear in that order.
"""
from __future__ import annotations

import importlib
import re
import unicodedata
from functools import lru_cache

LANGS = ("hi", "te", "ta", "kn", "ml", "bn", "mr", "es", "fr", "de", "pt", "ar", "ja", "ko", "zh")

# fmt: off
SYN: dict[str, dict[str, str]] = {
    "indoor": {
        "hi": "अंदर अन्दर भीतर कमरा कमरे रसोई दफ़्तर दफ्तर ऑफिस ऑफ़िस मकान बेडरूम कक्षा दुकान|घर में|घर के अंदर",
        "te": "లోపల లోపలికి ఇంట్లో ఇంటి ఇల్లు గది గదిలో వంటగది ఆఫీసు ఆఫీస్ కార్యాలయం దుకాణం హాల్",
        "ta": "உள்ளே உள்ளறை வீட்டில் வீடு வீட்டுக்குள் அறை அறையில் சமையலறை அலுவலகம் கடை",
        "kn": "ಒಳಗೆ ಒಳಗಡೆ ಮನೆಯಲ್ಲಿ ಮನೆ ಕೋಣೆ ಅಡುಗೆಮನೆ ಕಚೇರಿ ಅಂಗಡಿ",
        "ml": "അകത്ത് ഉള്ളിൽ വീട്ടിൽ വീട് മുറി മുറിയിൽ അടുക്കള ഓഫീസ് കട",
        "bn": "ভেতরে ভিতরে ঘরে ঘর বাড়িতে বাড়ি রান্নাঘর অফিস দোকান কক্ষ",
        "mr": "आत आतमध्ये घरात घर खोली खोलीत स्वयंपाकघर ऑफिस कार्यालय दुकान",
        "es": "interior int adentro dentro casa* habitación cuarto cocina oficina dormitorio pasillo salón tienda restaurante aula apartamento",
        "fr": "intérieur int dedans maison* chambre pièce cuisine bureau salon couloir appartement magasin restaurant",
        "de": "innen drinnen innenraum haus hause zimmer küche büro wohnung flur schlafzimmer laden restaurant klassenzimmer",
        "pt": "interior dentro casa quarto cozinha escritório sala corredor apartamento loja restaurante",
        "ar": "داخلي داخل غرفة بيت منزل مطبخ مكتب شقة متجر مطعم",
        "ja": "屋内 室内 部屋 家の中 家で 台所 キッチン オフィス 事務所 店内 教室 廊下 寝室 車内",
        "ko": "실내 안에서 집 집에서 집안 방 방에 방안 부엌 사무실 가게 교실 복도 거실|차 안",
        "zh": "室内 屋内 房间 屋里 家里 家中 厨房 办公室 卧室 走廊 教室 店里 车内 内景",
    },
    "outdoor": {
        "hi": "बाहर सड़क सड़कों गली जंगल वन समुद्र समंदर नदी खेत खेतों पहाड़ शहर गाँव गांव बाज़ार बाजार बारिश आसमान आकाश मैदान छत बगीचा बगीचे पार्क किनारे तट",
        "te": "బయట బయటకు బయటికి వీధి రోడ్డు రోడ్ అడవి సముద్రం సముద్ర నది పొలం పొలాల కొండ నగరం పట్నం ఊరు గ్రామం మార్కెట్ వర్షం వాన ఆకాశం మైదానం తోట పార్క్ తీరం బీచ్",
        "ta": "வெளியே வெளியில் தெரு சாலை காடு கடல் வயல் மலை நகரம் ஊர் கிராமம் சந்தை மழை வானம் மைதானம் தோட்டம் பூங்கா கடற்கரை",
        "kn": "ಹೊರಗೆ ಹೊರಗಡೆ ಬೀದಿ ರಸ್ತೆ ಕಾಡು ಸಮುದ್ರ ನದಿ ಹೊಲ ಬೆಟ್ಟ ನಗರ ಊರು ಹಳ್ಳಿ ಮಾರುಕಟ್ಟೆ ಮಳೆ ಆಕಾಶ ಮೈದಾನ ತೋಟ ಉದ್ಯಾನ ಕಡಲತೀರ",
        "ml": "പുറത്ത് പുറത്തേക്ക് തെരുവ് റോഡ് കാട് കടൽ പുഴ നദി വയൽ മല നഗരം ഗ്രാമം ചന്ത മഴ ആകാശം മൈതാനം തോട്ടം പാർക്ക് കടൽത്തീരം",
        "bn": "বাইরে রাস্তা রাস্তায় গলি জঙ্গল বন সমুদ্র নদী মাঠ পাহাড় শহর গ্রাম বাজার বৃষ্টি আকাশ বাগান পার্ক সৈকত",
        "mr": "बाहेर रस्ता रस्त्यावर गल्ली जंगल समुद्र नदी शेत डोंगर शहर गाव बाजार पाऊस आकाश मैदान बाग उद्यान किनारा",
        "es": "exterior ext afuera fuera calle* carretera camino bosque selva playa mar campo cielo jardín parque montaña* río pueblo aldea ciudad azotea desierto lluvia plaza",
        "fr": "extérieur ext dehors rue* route chemin forêt jungle plage mer champ* ciel jardin parc montagne* rivière fleuve village ville toit désert pluie",
        "de": "außen draußen straße* wald dschungel strand meer see feld himmel garten park berg* fluss dorf stadt dach wüste regen platz",
        "pt": "exterior fora rua* estrada caminho floresta selva praia mar campo céu jardim parque montanha* rio aldeia vila cidade telhado deserto chuva praça|lá fora",
        "ar": "خارجي خارج شارع طريق غابة شاطئ بحر حقل سماء حديقة جبل نهر قرية مدينة سطح صحراء مطر ساحة",
        "ja": "屋外 外で 外に 外へ 野外 通り 道路 森 林 ジャングル 浜辺 ビーチ 海 畑 空 庭 公園 山 川 村 街 町 都市 屋上 砂漠 雨",
        "ko": "실외 야외 밖 밖에서 바깥 거리 길 도로 숲 정글 해변 바다 들판 하늘 정원 공원 산 강 마을 도시 옥상 사막 비가 빗속",
        "zh": "室外 户外 外景 外面 街道 街上 马路 道路 森林 树林 丛林 海滩 沙滩 大海 海边 田野 天空 花园 公园 山上 河边 村庄 村子 城市 屋顶 沙漠 下雨 雨中",
    },
    "night": {
        "hi": "रात रातों रात्रि अंधेरा अंधेरे अँधेरा अँधेरे चाँद चांद चांदनी चाँदनी|रात को|आधी रात",
        "te": "రాత్రి అర్ధరాత్రి చీకటి చీకట్లో వెన్నెల చంద్రుడు",
        "ta": "இரவு இரவில் நள்ளிரவு இருட்டு இருள் நிலவு நிலா",
        "kn": "ರಾತ್ರಿ ಮಧ್ಯರಾತ್ರಿ ಕತ್ತಲು ಕತ್ತಲೆ ಬೆಳದಿಂಗಳು ಚಂದ್ರ",
        "ml": "രാത്രി അർദ്ധരാത്രി ഇരുട്ട് ഇരുട്ടിൽ നിലാവ് ചന്ദ്രൻ",
        "bn": "রাত রাতে রাত্রি মাঝরাত অন্ধকার অন্ধকারে চাঁদ জ্যোৎস্না",
        "mr": "रात्र रात्री मध्यरात्र मध्यरात्री अंधार अंधारात चंद्र चांदणे",
        "es": "noche* medianoche oscuridad oscuro oscura luna nocturno nocturna",
        "fr": "nuit* minuit obscurité sombre lune nocturne",
        "de": "nacht* mitternacht dunkel dunkelheit mond mondlicht",
        "pt": "noite* escuridão escuro escura lua noturno noturna",
        "ar": "ليل ليلا ليلاً ليلة ظلام قمر|منتصف الليل",
        "ja": "夜 夜中 深夜 真夜中 暗闇 暗い 月明かり 月夜 ナイト",
        "ko": "밤 밤에 한밤중 자정 어둠 어두운 달빛 야간",
        "zh": "夜 夜晚 晚上 夜里 深夜 午夜 半夜 黑暗 漆黑 月光 夜景",
    },
    "day": {
        "hi": "दिन दिनभर दोपहर सुबह धूप उजाला उजाले|दिन में",
        "te": "పగలు పగటి మధ్యాహ్నం ఉదయం ఎండ ఎండలో వెలుగు పగటిపూట",
        "ta": "பகல் பகலில் மதியம் காலை வெயில் வெயிலில் வெளிச்சம்",
        "kn": "ಹಗಲು ಮಧ್ಯಾಹ್ನ ಬೆಳಿಗ್ಗೆ ಬಿಸಿಲು ಬಿಸಿಲಿನಲ್ಲಿ ಬೆಳಕು",
        "ml": "പകൽ ഉച്ച ഉച്ചയ്ക്ക് രാവിലെ വെയിൽ വെയിലത്ത് വെളിച്ചം",
        "bn": "দিন দিনে দুপুর সকাল সকালে রোদ রোদে আলো",
        "mr": "दिवस दिवसा दुपार सकाळ सकाळी ऊन उन्हात उजेड",
        "es": "día días mañana mediodía sol soleado soleada|luz del sol",
        "fr": "jour journée matin midi soleil ensoleillé ensoleillée",
        "de": "tag tags tagsüber morgen mittag nachmittag sonne sonnig sonnenlicht",
        "pt": "dia dias manhã sol ensolarado ensolarada|meio dia",
        "ar": "نهار نهارا صباح شمس مشمس",
        "ja": "昼 昼間 日中 朝 午後 真昼 日差し 晴れ 太陽 陽光",
        "ko": "낮 낮에 한낮 아침 오후 정오 햇빛 햇살 맑은 주간",
        "zh": "白天 白日 日间 中午 早上 上午 下午 阳光 晴天 太阳 日景",
    },
    "dawn": {
        "hi": "भोर सूर्योदय सवेरा सवेरे", "te": "తెల్లవారు తెల్లవారుజాము సూర్యోదయం వేకువ ప్రభాతం",
        "ta": "விடியல் விடியற்காலை அதிகாலை|சூரிய உதயம்", "kn": "ಮುಂಜಾನೆ ಸೂರ್ಯೋದಯ|ಬೆಳಗಿನ ಜಾವ",
        "ml": "പുലരി പുലർച്ചെ സൂര്യോദയം പ്രഭാതം", "bn": "ভোর ভোরে সূর্যোদয় প্রভাত", "mr": "पहाट पहाटे सूर्योदय",
        "es": "amanecer alba madrugada", "fr": "aube aurore|lever du soleil",
        "de": "morgengrauen morgendämmerung sonnenaufgang tagesanbruch", "pt": "amanhecer alvorada madrugada|nascer do sol",
        "ar": "فجر شروق", "ja": "夜明け 明け方 日の出 早朝", "ko": "새벽 동틀녘 일출 해돋이", "zh": "黎明 清晨 拂晓 日出 破晓",
    },
    "dusk": {
        "hi": "शाम साँझ सांझ संध्या सूर्यास्त गोधूलि", "te": "సంధ్య సాయంత్రం సాయంకాలం సూర్యాస్తమయం",
        "ta": "அந்தி மாலை சாயங்காலம்|சூரிய அஸ்தமனம்", "kn": "ಸಂಜೆ ಮುಸ್ಸಂಜೆ ಸೂರ್ಯಾಸ್ತ ಗೋಧೂಳಿ",
        "ml": "സന്ധ്യ വൈകുന്നേരം വൈകിട്ട് സൂര്യാസ്തമയം", "bn": "সন্ধ্যা সন্ধ্যায় বিকেল গোধূলি সূর্যাস্ত",
        "mr": "संध्याकाळ संध्याकाळी सायंकाळ सूर्यास्त", "es": "atardecer anochecer crepúsculo ocaso|puesta de sol",
        "fr": "crépuscule soir soirée|coucher du soleil", "de": "dämmerung abend abends sonnenuntergang abenddämmerung",
        "pt": "entardecer anoitecer crepúsculo|pôr do sol|fim de tarde", "ar": "غسق غروب مساء",
        "ja": "夕暮れ 夕方 黄昏 夕日 日没 夕焼け", "ko": "해질녘 저녁 황혼 노을 석양 일몰", "zh": "黄昏 傍晚 夕阳 日落 晚霞 薄暮",
    },
    "fast": {
        "hi": "दौड़ दौड़ता दौड़ती दौड़ते भागता भागती भागते भागना लड़ाई लड़ते मारपीट पीछा तेज़ तेजी तेज़ी धमाका विस्फोट नाचता नाचती नाच भगदड़ जल्दी टक्कर एक्शन",
        "te": "పరుగు పరిగెత్తు పరుగెత్తు పరుగెడు పోరాటం పోరాడు కొట్లాట ఫైట్ ఛేజ్ వెంబడి వెంటాడు వేగం వేగంగా పేలుడు డ్యాన్స్ నృత్యం తప్పించుకు హడావుడి యాక్షన్",
        "ta": "ஓடுகி ஓடி ஓட்டம் சண்டை துரத்து துரத்தல் வேகம் வேகமாக வெடிப்பு நடனம் ஆட்டம் தப்பி அவசரம் ஆக்ஷன்",
        "kn": "ಓಡುತ್ತ ಓಡಿ ಓಟ ಹೋರಾಟ ಜಗಳ ಫೈಟ್ ಬೆನ್ನಟ್ಟು ವೇಗ ವೇಗವಾಗಿ ಸ್ಫೋಟ ನೃತ್ಯ ಡ್ಯಾನ್ಸ್ ತಪ್ಪಿಸಿಕೊ ಆತುರ",
        "ml": "ഓട്ടം ഓടുന്നു ഓടി പോരാട്ടം സംഘട്ടനം പിന്തുടര് ചേസ് വേഗം വേഗത്തിൽ സ്ഫോടനം നൃത്തം ഡാൻസ് രക്ഷപ്പെട തിടുക്കം",
        "bn": "দৌড় দৌড়ায় দৌড়াচ্ছে ছুটছে ছোটে লড়াই মারামারি তাড়া ধাওয়া দ্রুত বিস্ফোরণ নাচ পালায় পালাচ্ছে তাড়াহুড়ো",
        "mr": "धाव धावतो धावते धावत पळतो पळते पळत लढाई मारामारी पाठलाग वेगवान वेगाने स्फोट नाच नृत्य घाई",
        "es": "corre corren correr corriendo carrera persecución persigue pelea pelean peleando lucha rápido rápida rápidamente explosión baile bailan bailando escapa escape huye huyendo choque prisa acción",
        "fr": "court courent courir course poursuite poursuit combat bagarre rapide rapidement explosion danse dansent fuit fuite échappe pressé hâte action|se battent",
        "de": "rennt rennen laufen läuft verfolgung verfolgt kampf kämpft kämpfen prügelei schnell explosion tanz tanzt tanzen flucht flieht eile hektisch action",
        "pt": "corre correm correr correndo perseguição persegue luta lutam briga rápido rápida explosão dança dançam foge fuga escapa pressa ação",
        "ar": "يركض ركض تركض يجري مطاردة يطارد قتال معركة شجار سريع بسرعة انفجار رقص يرقص هروب يهرب عجلة",
        "ja": "走る 走り 走っ 駆け 追いかけ 追跡 戦い 戦う 喧嘩 ケンカ 格闘 速い 素早 爆発 踊る 踊り ダンス 逃げ 逃走 急い アクション カーチェイス",
        "ko": "달리 뛰어 뛴다 추격 쫓아 싸움 싸우 격투 빠르게 빠른 폭발 춤 춤추 도망 탈출 서둘러 액션",
        "zh": "奔跑 跑 追逐 追赶 打斗 打架 战斗 快速 飞快 爆炸 跳舞 舞蹈 逃跑 逃离 匆忙 动作 追车",
    },
    "still": {
        "hi": "शांत शान्त चुप चुपचाप स्थिर बैठा बैठी बैठे बैठकर इंतज़ार इंतजार प्रतीक्षा धीरे खामोश ख़ामोश खामोशी सन्नाटा",
        "te": "నిశ్శబ్దం నిశ్శబ్ద వేచి నిశ్చలం ప్రశాంతం ప్రశాంతంగా మౌనం కూర్చు ఎదురుచూ నిదానం నెమ్మది",
        "ta": "அமைதி அமைதியாக மௌனம் நிச்சலனம் உட்கார் அமர்ந் காத்திரு மெதுவாக நிதானம்",
        "kn": "ಶಾಂತ ಮೌನ ನಿಶ್ಯಬ್ದ ಸ್ಥಿರ ಕುಳಿತ ಕೂತ ಕಾಯುತ್ತ ನಿಧಾನ",
        "ml": "ശാന്തം ശാന്തമായി നിശ്ശബ്ദം നിശബ്ദ മൗനം നിശ്ചലം ഇരിക്കുന്നു ഇരുന്ന് കാത്തിരി പതുക്കെ",
        "bn": "শান্ত চুপ চুপচাপ নীরব নিস্তব্ধ স্থির বসে অপেক্ষা ধীরে",
        "mr": "शांत शांतपणे गप्प स्तब्ध स्थिर बसला बसली बसून प्रतीक्षा हळू हळूहळू|वाट पाहत",
        "es": "calma tranquilo tranquila quieto quieta silencio silencioso silenciosa sentado sentada espera esperando lento lentamente despacio inmóvil|se sienta",
        "fr": "calme tranquille immobile silence silencieux silencieuse assis assise assoit attend attendant lent lentement",
        "de": "ruhig still stille schweigen sitzt sitzend wartet warten langsam regungslos",
        "pt": "calma calmo tranquilo tranquila parado parada silêncio silencioso sentado sentada senta espera esperando devagar lento lentamente imóvel",
        "ar": "هادئ هدوء بهدوء صمت صامت ساكن يجلس تجلس جالس جالسة ينتظر تنتظر انتظار ببطء بطيء",
        "ja": "静か 静寂 沈黙 静止 座っ 座る 待つ 待っ ゆっくり じっと 穏やか",
        "ko": "조용 고요 침묵 정적 앉아 앉은 기다리 기다린 천천히 가만히 차분",
        "zh": "安静 平静 寂静 沉默 静止 坐着 坐在 等待 等着 慢慢 缓缓 一动不动",
    },
    "crowd": {
        "hi": "भीड़ लोग लोगों परिवार दोस्त दोस्तों शादी सभा पार्टी मेला जुलूस दर्शक|सब लोग",
        "te": "గుంపు జనం ప్రజలు మనుషులు కుటుంబం స్నేహితులు పెళ్ళి పెళ్లి సభ పార్టీ అందరూ జాతర ప్రేక్షకులు",
        "ta": "கூட்டம் மக்கள் குடும்பம் நண்பர்கள் கல்யாணம் திருமணம் விழா பார்ட்டி எல்லோரும் பார்வையாளர்கள்",
        "kn": "ಗುಂಪು ಜನ ಜನರು ಕುಟುಂಬ ಸ್ನೇಹಿತರು ಮದುವೆ ಸಭೆ ಪಾರ್ಟಿ ಎಲ್ಲರೂ ಜಾತ್ರೆ ಪ್ರೇಕ್ಷಕರು",
        "ml": "ആൾക്കൂട്ടം ജനക്കൂട്ടം ആളുകൾ കുടുംബം സുഹൃത്തുക്കൾ കല്യാണം വിവാഹം പാർട്ടി എല്ലാവരും ഉത്സവം",
        "bn": "ভিড় লোকজন মানুষজন পরিবার বন্ধুরা বিয়ে সভা পার্টি সবাই মেলা দর্শক",
        "mr": "गर्दी लोक कुटुंब मित्र लग्न सभा पार्टी सगळे जत्रा प्रेक्षक",
        "es": "multitud gente muchedumbre fiesta familia amigos juntos todos boda reunión público equipo",
        "fr": "foule gens fête famille amis ensemble tous mariage réunion public équipe",
        "de": "menge menschenmenge leute menschen party feier familie freunde zusammen alle hochzeit versammlung publikum team",
        "pt": "multidão gente pessoas festa família amigos juntos todos casamento reunião público equipe",
        "ar": "حشد زحام ناس حفلة عائلة أصدقاء معا معاً الجميع عرس زفاف اجتماع جمهور",
        "ja": "群衆 人々 大勢 人混み パーティー 家族 友達 仲間 みんな 結婚式 会議 観客 祭り",
        "ko": "군중 사람들 인파 파티 가족 친구들 함께 모두 결혼식 회의 관객 축제",
        "zh": "人群 人们 众人 大家 派对 聚会 家人 家庭 朋友们 一起 婚礼 会议 观众 节日",
    },
    "alone": {
        "hi": "अकेला अकेली अकेले सुनसान वीरान तन्हा तन्हाई एकांत", "te": "ఒంటరి ఏకాంతం నిర్జన|ఎవరూ లేని",
        "ta": "தனிமை தனியாக தனியே தனிமையில் வெறிச்சோடிய யாருமில்லாத", "kn": "ಏಕಾಂಗಿ ಒಬ್ಬನೇ ಒಬ್ಬಳೇ ಒಂಟಿ ನಿರ್ಜನ",
        "ml": "ഏകാന്തം ഒറ്റയ്ക്ക് തനിച്ച് തനിയെ വിജനം", "bn": "একা একাকী নির্জন ফাঁকা", "mr": "एकटा एकटी एकटे एकांत निर्जन ओसाड",
        "es": "solo sola solitario solitaria soledad vacío vacía nadie", "fr": "seul seule solitaire solitude vide",
        "de": "allein alleine einsam einsamkeit leer verlassen niemand", "pt": "sozinho sozinha solitário solitária solidão vazio vazia ninguém",
        "ar": "وحيد وحيدة وحده وحدها بمفرده بمفردها وحدة فارغ خالي مهجور", "ja": "ひとり 一人 独り 孤独 誰もいない 無人 寂しい",
        "ko": "혼자 홀로 외로운 외로이 고독 아무도|텅 빈", "zh": "独自 一个人 孤独 孤单 独处 空无一人 空荡荡",
    },
    "person": {
        "hi": "आदमी औरत महिला लड़का लड़की माँ मां पिता बच्चा बच्चे व्यक्ति नायक नायिका यात्री",
        "te": "అతను ఆమె మనిషి వ్యక్తి అబ్బాయి అమ్మాయి తల్లి తండ్రి అమ్మ నాన్న హీరో వ్యోమగామి",
        "ta": "அவன் அவள் மனிதன் பெண் ஆண் சிறுவன் சிறுமி அம்மா அப்பா குழந்தை நபர் நாயகன் நாயகி",
        "kn": "ಅವನು ಅವಳು ಮನುಷ್ಯ ವ್ಯಕ್ತಿ ಹುಡುಗ ಹುಡುಗಿ ಅಮ್ಮ ಅಪ್ಪ ತಾಯಿ ತಂದೆ ಮಗು ನಾಯಕ",
        "ml": "അവൻ അവൾ മനുഷ്യൻ വ്യക്തി പുരുഷൻ സ്ത്രീ ആൺകുട്ടി പെൺകുട്ടി അമ്മ അച്ഛൻ കുട്ടി നായകൻ",
        "bn": "লোকটি মানুষ ব্যক্তি ছেলে মেয়ে মা বাবা শিশু নায়ক নায়িকা",
        "mr": "माणूस व्यक्ती मुलगा मुलगी आई वडील बाबा नायक",
        "es": "él ella hombre mujer chico chica niño niña madre padre persona héroe heroína astronauta",
        "fr": "il elle homme femme garçon fille enfant mère père héros héroïne astronaute",
        "de": "er mann frau junge mädchen kind mutter vater held heldin astronaut astronautin",
        "pt": "ele ela homem mulher menino menina criança mãe pai pessoa herói heroína astronauta",
        "ar": "هو هي رجل امرأة ولد فتاة بنت طفل شخص بطل بطلة|رائد فضاء",
        "ja": "彼 彼女 男 女 少年 少女 子供 母 父 人物 主人公 ヒーロー 宇宙飛行士",
        "ko": "그는 그녀 남자 여자 소년 소녀 아이 엄마 어머니 아빠 아버지 인물 주인공 영웅 우주비행사",
        "zh": "他 她 男人 女人 男孩 女孩 孩子 母亲 妈妈 父亲 爸爸 人物 主角 英雄 宇航员",
    },
    "close": {
        "hi": "क्लोज़ क्लोज क्लोज़अप चेहरा चेहरे आँखें आंखें आँखों आंखों हाथ होंठ फुसफुसा",
        "te": "క్లోజ్ క్లోజప్ ముఖం ముఖము మొహం కళ్ళు కళ్లు చేతులు పెదవులు గుసగుస",
        "ta": "க்ளோஸ் க்ளோசப் முகம் கண்கள் கைகள் உதடுகள் கிசுகிசு",
        "kn": "ಕ್ಲೋಸ್ ಕ್ಲೋಸಪ್ ಮುಖ ಕಣ್ಣು ತುಟಿ ಪಿಸುಗುಟ್ಟು",
        "ml": "ക്ലോസ് ക്ലോസപ്പ് മുഖം കണ്ണുകൾ കൈകൾ ചുണ്ടുകൾ",
        "bn": "ক্লোজ ক্লোজআপ মুখ মুখে চোখ চোখে হাত ঠোঁট ফিসফিস", "mr": "क्लोज क्लोजअप चेहरा चेहऱ्या डोळे हात ओठ कुजबुज",
        "es": "primerísimo cara rostro ojos manos mano lágrimas detalle labios susurra susurro|primer plano",
        "fr": "visage yeux mains main larmes détail lèvres chuchote murmure|gros plan",
        "de": "nahaufnahme großaufnahme gesicht augen hände hand tränen detail lippen flüstert",
        "pt": "close rosto cara olhos mãos mão lágrimas detalhe lábios sussurra|primeiro plano|plano fechado",
        "ar": "وجه وجهها وجهه عيون عينين عيناه يد يدين دموع تفاصيل شفاه همس|لقطة قريبة",
        "ja": "クローズアップ アップ 顔 瞳 手元 両手 唇 涙 ささやく 囁", "ko": "클로즈업 클로즈 얼굴 눈빛 눈물 손 입술 속삭",
        "zh": "特写 近景 脸 脸庞 面孔 眼睛 双眼 双手 嘴唇 眼泪 泪水 低语 细节",
    },
    "wide": {
        "hi": "वाइड विहंगम क्षितिज नज़ारा नजारा लैंडस्केप परिदृश्य|हवाई दृश्य", "te": "వైడ్ విశాల హోరిజోన్ క్షితిజం ఏరియల్ ల్యాండ్స్కేప్",
        "ta": "வைட் அகலமான பரந்த அடிவானம் வான்வழி நிலப்பரப்பு", "kn": "ವೈಡ್ ವಿಶಾಲ ದಿಗಂತ ಏರಿಯಲ್ ಭೂದೃಶ್ಯ",
        "ml": "വൈഡ് വിശാലമായ ചക്രവാളം ആകാശദൃശ്യം ഭൂപ്രകൃതി", "bn": "ওয়াইড বিস্তৃত দিগন্ত|প্রাকৃতিক দৃশ্য",
        "mr": "वाइड विस्तीर्ण क्षितिज विहंगम निसर्गदृश्य",
        "es": "panorámica panorama paisaje horizonte aérea aéreo|plano general|plano abierto",
        "fr": "panorama panoramique paysage horizon aérien aérienne|plan large|plan d ensemble",
        "de": "totale weitwinkel panorama landschaft horizont luftaufnahme aussicht",
        "pt": "panorâmica panorama paisagem horizonte aérea aéreo|plano geral|plano aberto",
        "ar": "بانوراما منظر أفق جوية|لقطة عامة|لقطة واسعة", "ja": "ロングショット 引きの画 全景 風景 景色 パノラマ 地平線 空撮 俯瞰",
        "ko": "와이드 롱샷 전경 풍경 파노라마 지평선 항공 부감", "zh": "远景 全景 大全景 风景 景色 全貌 地平线 航拍 鸟瞰 俯瞰",
    },
    "sad": {
        "hi": "उदास दुख दुःख दुखी रोना रोती रोता शोक ग़म गम|रो रही|रो रहा", "te": "విషాదం దుఃఖం బాధ ఏడుపు ఏడుస్తు కన్నీళ్లు కన్నీరు శోకం విచారం",
        "ta": "சோகம் துக்கம் வருத்தம் அழுகை அழுகிறா கண்ணீர்", "kn": "ದುಃಖ ಬೇಸರ ಅಳುತ್ತ ಕಣ್ಣೀರು ಶೋಕ",
        "ml": "ദുഃഖം സങ്കടം കരച്ചിൽ കരയുന്നു കണ്ണീർ വിഷാദം", "bn": "দুঃখ দুঃখী কান্না কাঁদছে কাঁদে শোক বিষণ্ণ অশ্রু|মন খারাপ",
        "mr": "दुःख दुःखी उदास रडणे रडत रडते रडतो शोक अश्रू", "es": "triste tristeza llora llorando llanto duelo pérdida pena",
        "fr": "triste tristesse pleure pleurs deuil chagrin perte", "de": "traurig trauer trauert weint weinen verlust kummer",
        "pt": "triste tristeza chora chorando choro luto perda", "ar": "حزين حزينة حزن يبكي تبكي بكاء حداد فقدان",
        "ja": "悲しい 悲しみ 泣く 泣い 喪失 寂しさ", "ko": "슬픈 슬픔 슬퍼 울고 운다 울음 상실", "zh": "悲伤 伤心 难过 哭泣 哭 哀伤 失去",
    },
    "tense": {
        "hi": "डर डरा डरी ख़तरा खतरा खतरे तनाव सस्पेंस घबराया घबराई घबराहट भय खौफ़ ख़ौफ़", "te": "ఉత్కంఠ భయం భయపడు ప్రమాదం టెన్షన్ ఆందోళన బెదిరింపు",
        "ta": "பதற்றம் பயம் பயந்து ஆபத்து அச்சுறுத்தல் திகில் டென்ஷன்", "kn": "ಭಯ ಅಪಾಯ ಆತಂಕ ಟೆನ್ಷನ್ ಬೆದರಿಕೆ ಉದ್ವೇಗ",
        "ml": "ഉദ്വേഗം ഭയം പേടി അപകടം ഭീഷണി ടെൻഷൻ ആശങ്ക", "bn": "উত্তেজনা ভয় ভীত বিপদ হুমকি টেনশন আতঙ্ক",
        "mr": "तणाव भीती घाबर धोका धमकी टेन्शन थरार", "es": "tenso tensa tensión miedo peligro suspenso suspense amenaza asustado asustada nervioso nerviosa",
        "fr": "tendu tendue tension peur danger suspense menace effrayé effrayée nerveux nerveuse angoisse",
        "de": "angespannt spannung angst gefahr bedrohung verängstigt nervös unheimlich",
        "pt": "tenso tensa tensão medo perigo suspense ameaça assustado assustada nervoso nervosa",
        "ar": "متوتر متوترة توتر خوف خائف خائفة خطر تهديد تشويق رعب", "ja": "緊張 恐怖 怖い 危険 不安 脅威 サスペンス 怯え",
        "ko": "긴장 공포 무서 위험 불안 위협 서스펜스 겁에", "zh": "紧张 恐惧 害怕 危险 悬疑 威胁 不安 惊恐",
    },
    "warm": {
        "hi": "खुश ख़ुश खुशी ख़ुशी प्यार मुस्कान मुस्कुरा हँसी हंसी हँसते जश्न उत्सव|गले लग", "te": "సంతోషం ఆనందం ప్రేమ నవ్వు చిరునవ్వు కౌగిలి వేడుక సంబరం",
        "ta": "மகிழ்ச்சி சந்தோஷம் அன்பு காதல் சிரிப்பு புன்னகை அணைப்பு கொண்டாட்டம்", "kn": "ಸಂತೋಷ ಖುಷಿ ಪ್ರೀತಿ ನಗು ಮುಗುಳ್ನಗೆ ಅಪ್ಪುಗೆ ಆಚರಣೆ",
        "ml": "സന്തോഷം ആനന്ദം സ്നേഹം പ്രണയം ചിരി പുഞ്ചിരി ആലിംഗനം ആഘോഷം", "bn": "খুশি আনন্দ ভালোবাসা প্রেম হাসি হাসছে জড়িয়ে উৎসব",
        "mr": "आनंद आनंदी प्रेम हसू हसत स्मित मिठी उत्सव", "es": "feliz felices alegría alegre amor sonríe sonrisa ríe risa celebra celebración abrazo abraza",
        "fr": "heureux heureuse joie amour sourit sourire rit rire célébration câlin embrasse",
        "de": "glücklich freude liebe herzlich lächelt lächeln lacht lachen feiert umarmt umarmung",
        "pt": "feliz felizes alegria alegre amor sorri sorriso riso risada celebra comemora abraço abraça",
        "ar": "سعيد سعيدة سعادة فرح حب يبتسم تبتسم ابتسامة يضحك تضحك ضحك احتفال عناق",
        "ja": "幸せ 喜び 嬉しい 愛 温かい 温か 笑顔 笑う 笑い 微笑 祝 抱きしめ ハグ", "ko": "행복 기쁨 사랑 따뜻 미소 웃음 웃는 웃으 축하 포옹 안아",
        "zh": "快乐 幸福 开心 喜悦 爱 温暖 微笑 笑容 欢笑 笑 庆祝 拥抱",
    },
    "titles": {
        "hi": "टाइटल शीर्षक", "te": "టైటిల్ టైటిల్స్ శీర్షిక", "ta": "தலைப்பு தலைப்புகள் டைட்டில் டைட்டில்ஸ்", "kn": "ಶೀರ್ಷಿಕೆ ಟೈಟಲ್ ಟೈಟಲ್ಸ್",
        "ml": "ടൈറ്റിൽ ടൈറ്റിലുകൾ ശീർഷകം", "bn": "টাইটেল শিরোনাম", "mr": "शीर्षक शीर्षके टायटल", "es": "título títulos cabecera|créditos iniciales",
        "fr": "titre titres|générique de début", "de": "titel vorspann titelsequenz", "pt": "título títulos|créditos iniciais",
        "ar": "عنوان عناوين|شارة البداية", "ja": "タイトル オープニング 題名", "ko": "타이틀 제목 오프닝", "zh": "片头 标题 开场字幕",
    },
    "credits": {
        "hi": "क्रेडिट क्रेडिट्स समाप्त समाप्ति|द एंड", "te": "క్రెడిట్స్ క్రెడిట్ శుభం ముగింపు", "ta": "கிரெடிட்ஸ் கிரெடிட் முடிவு சுபம்",
        "kn": "ಕ್ರೆಡಿಟ್ಸ್ ಕ್ರೆಡಿಟ್ ಮುಕ್ತಾಯ ಶುಭಂ", "ml": "ക്രെഡിറ്റുകൾ ക്രെഡിറ്റ് ശുഭം അവസാനം", "bn": "ক্রেডিট সমাপ্ত",
        "mr": "श्रेयनामावली क्रेडिट समाप्त", "es": "créditos fin|créditos finales", "fr": "générique fin|générique de fin",
        "de": "abspann ende", "pt": "créditos fim|créditos finais", "ar": "تترات نهاية|شارة النهاية",
        "ja": "クレジット エンドロール エンドクレジット 終わり", "ko": "크레딧 엔딩 끝", "zh": "片尾 演职员表 字幕表 剧终",
    },
}
# fmt: on

# translated cue labels that are too ambiguous to search for on their own
EXCLUDE = {"日", "快", "静", "近", "远", "寄り", "引き", "قريبة", "عامة", "nah", "weit", "warm", "close", "solo"}

_SPLIT = re.compile(r"[\s.,;:!?¡¿।॥()\[\]{}\"'`“”‘’«»—–\-/\\|…·•。、！？，；：「」『』（）【】؟،؛*_#=+<>~^]+")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
_HANGUL = re.compile(r"[가-힯]")
_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[a-zà-ÿœæß]")
_AR_PREFIX = ("وال", "بال", "فال", "كال", "لل", "ال", "و", "ب", "ف", "ل", "ك")


def norm(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").casefold()
    return t.replace("‌", "").replace("‍", "").replace("­", "")


def _kind(word: str) -> str:
    if _CJK.search(word):
        return "cjk"
    if _HANGUL.search(word):
        return "ko"
    if _ARABIC.search(word):
        return "ar"
    if _LATIN.search(word):
        return "latin"
    return "indic"


def _prefix_ok(word: str, kind: str) -> bool:
    return {"ko": 2, "ar": 3, "indic": 4}.get(kind, 10 ** 6) <= len(word)


@lru_cache(maxsize=1)
def _index():
    """→ (cjk: [(word, cue)] longest first, tokens: {word: (cues, prefix)}, phrases: [(tokens, cues, prefix_last)])"""
    pairs: list[tuple[str, str]] = []
    for cue, per_lang in SYN.items():
        for words in per_lang.values():
            single, *multi = words.split("|")          # "word word word|a phrase|another phrase"
            pairs.extend((w, cue) for w in single.split())
            pairs.extend((p.strip(), cue) for p in multi if p.strip())
    for lang in LANGS:                                   # every language's translated cue label
        try:
            mod = importlib.import_module(f"cinevision.director.i18n.{lang}")
        except ModuleNotFoundError:
            continue
        for cue, label in getattr(mod, "CUES", {}).items():
            if label and label not in EXCLUDE and cue in SYN:
                pairs.append((label, cue))
    cjk: dict[str, set] = {}
    tokens: dict[str, list] = {}
    phrases: dict[tuple, list] = {}
    for raw, cue in pairs:
        star = raw.endswith("*")
        w = norm(raw.rstrip("*"))
        if not w:
            continue
        kind = _kind(w)
        if kind == "cjk":
            for part in w.split():
                cjk.setdefault(part, set()).add(cue)
            continue
        toks = [t for t in _SPLIT.split(w) if t]
        if not toks:
            continue
        prefix = star or _prefix_ok(toks[-1], kind)
        if len(toks) == 1:
            ent = tokens.setdefault(toks[0], [set(), False])
            ent[0].add(cue)
            ent[1] = ent[1] or prefix
        else:
            ent = phrases.setdefault(tuple(toks), [set(), False])
            ent[0].add(cue)
            ent[1] = ent[1] or prefix
    cjk_list = sorted(cjk.items(), key=lambda kv: -len(kv[0]))
    prefixed = sorted(((w, c) for w, (c, p) in tokens.items() if p), key=lambda kv: -len(kv[0]))
    exact = {w: c for w, (c, p) in tokens.items()}
    return cjk_list, exact, prefixed, [(k, c, p) for k, (c, p) in phrases.items()]


def _variants(tok: str) -> list[str]:
    out = [tok]
    if _ARABIC.search(tok):
        out += [tok[len(p):] for p in _AR_PREFIX if tok.startswith(p) and len(tok) - len(p) >= 2]
    return out


def match(text: str) -> set[str]:
    """Cues found in a non-English description (English is handled by engine.CUES)."""
    t = norm(text)
    if not t:
        return set()
    cjk, exact, prefixed, phrases = _index()
    found: set[str] = set()
    if _CJK.search(t):
        taken = [False] * len(t)
        for word, cues in cjk:
            start = t.find(word)
            while start >= 0:
                end = start + len(word)
                if not any(taken[start:end]):
                    found |= cues
                    for i in range(start, end):
                        taken[i] = True
                start = t.find(word, end)
    toks = [x for x in _SPLIT.split(t) if x]
    for tok in toks:
        for v in _variants(tok):
            if v in exact:
                found |= exact[v]
                break
            hit = next((c for w, c in prefixed if v.startswith(w)), None)
            if hit:
                found |= hit
                break
    if phrases:
        for i in range(len(toks)):
            for ph, cues, pre in phrases:
                n = len(ph)
                if i + n <= len(toks) and all(toks[i + j] == ph[j] for j in range(n - 1)) and \
                        (toks[i + n - 1] == ph[-1] or (pre and toks[i + n - 1].startswith(ph[-1]))):
                    found |= cues
    return found
