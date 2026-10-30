// GENERATED from backend/cinevision/concepts/{archetypes,lighting,builder,geometry}.py by scripts/gen_engine_data.py — do not edit.
export const ARCHETYPES: Record<string, any> = {
 "naturalistic": {
  "name": "Naturalistic Realism",
  "objective": "Make the moment feel observed rather than staged, letting performance carry the scene.",
  "emotion": "honest, grounded, intimate",
  "keywords": [
   "drama",
   "realism",
   "documentary",
   "honest",
   "grounded",
   "natural",
   "intimate",
   "slice of life",
   "indie",
   "calm",
   "sad",
   "grief",
   "love",
   "quiet"
  ],
  "treatment": "realistic",
  "movement": 1,
  "lighting": 1,
  "min_budget": "micro",
  "size_step": 0,
  "angle": "eye level",
  "composition": "three-quarter",
  "lens_ff": [
   32,
   50
  ],
  "aperture": "T2.8–T4",
  "dof": "moderate — subject sharp, background readable",
  "moves": [
   "handheld (breathing)",
   "static"
  ],
  "speed": 0.15,
  "light": {
   "style": "motivated soft light",
   "ratio": 2.5,
   "key_az": 40,
   "key_el": 25,
   "softness": 0.8,
   "haze": false,
   "back": 0.35,
   "negative_fill": false
  },
  "grade": {
   "sat": 1.0,
   "light": 1.0,
   "contrast": "gentle S-curve, true-to-life skin",
   "shift": 0
  },
  "transition": "straight cuts on action",
  "rhythm": "performance-led; hold shots until the beat lands",
  "complexity": 2
 },
 "chiaroscuro": {
  "name": "High-Contrast Chiaroscuro",
  "objective": "Carve the subject out of darkness to heighten conflict and moral weight.",
  "emotion": "dramatic, intense, conflicted",
  "keywords": [
   "drama",
   "crime",
   "noir",
   "thriller",
   "intense",
   "conflict",
   "power",
   "anger",
   "dramatic",
   "dark",
   "confrontation",
   "tension"
  ],
  "treatment": "balanced",
  "movement": 1,
  "lighting": 3,
  "min_budget": "low",
  "size_step": -1,
  "angle": "low angle",
  "composition": "profile-leaning three-quarter",
  "lens_ff": [
   40,
   65
  ],
  "aperture": "T2–T2.8",
  "dof": "shallow — background falls into shadow",
  "moves": [
   "slow push-in",
   "static"
  ],
  "speed": 0.08,
  "light": {
   "style": "hard side key with deep negative fill (Rembrandt → split)",
   "ratio": 8,
   "key_az": 75,
   "key_el": 35,
   "softness": 0.25,
   "haze": false,
   "back": 0.6,
   "negative_fill": true
  },
  "grade": {
   "sat": 0.8,
   "light": 0.85,
   "contrast": "crushed blacks, protected highlights",
   "shift": 0
  },
  "transition": "hard cuts, occasional smash cut",
  "rhythm": "deliberate; tension builds through stillness",
  "complexity": 3
 },
 "atmospheric": {
  "name": "Soft Atmospheric",
  "objective": "Wrap the scene in light and air so emotion reads as memory or longing.",
  "emotion": "tender, wistful, dreamlike",
  "keywords": [
   "romance",
   "romantic",
   "dream",
   "memory",
   "nostalgia",
   "wistful",
   "tender",
   "longing",
   "hope",
   "fantasy",
   "music video",
   "melancholy",
   "love"
  ],
  "treatment": "balanced",
  "movement": 2,
  "lighting": 3,
  "min_budget": "low",
  "size_step": 0,
  "angle": "eye level",
  "composition": "three-quarter with foreground framing",
  "lens_ff": [
   85,
   135
  ],
  "aperture": "T1.4–T2",
  "dof": "very shallow — creamy background, glowing highlights",
  "moves": [
   "slow drift (gimbal/slider)",
   "slow arc"
  ],
  "speed": 0.12,
  "light": {
   "style": "large soft source + strong backlight through haze",
   "ratio": 2,
   "key_az": 30,
   "key_el": 20,
   "softness": 0.95,
   "haze": true,
   "back": 0.9,
   "negative_fill": false
  },
  "grade": {
   "sat": 0.8,
   "light": 1.12,
   "contrast": "lifted blacks, soft roll-off, halation",
   "shift": 8
  },
  "transition": "soft dissolves and J-cuts",
  "rhythm": "lingering, musical",
  "complexity": 3
 },
 "kinetic": {
  "name": "Kinetic Energy",
  "objective": "Put the audience inside the action with movement that never quite settles.",
  "emotion": "urgent, energetic, chaotic",
  "keywords": [
   "action",
   "chase",
   "sport",
   "fight",
   "energy",
   "urgent",
   "panic",
   "chaos",
   "music video",
   "comedy",
   "excitement",
   "thrill",
   "run"
  ],
  "treatment": "balanced",
  "movement": 4,
  "lighting": 2,
  "min_budget": "micro",
  "size_step": 0,
  "angle": "eye level",
  "composition": "frontal and point-of-view",
  "lens_ff": [
   18,
   28
  ],
  "aperture": "T2.8–T4",
  "dof": "deep enough to keep moving subjects sharp",
  "moves": [
   "handheld tracking",
   "whip pan",
   "gimbal follow"
  ],
  "speed": 1.1,
  "light": {
   "style": "practical-heavy, high-contrast mixed colour",
   "ratio": 4,
   "key_az": 50,
   "key_el": 30,
   "softness": 0.45,
   "haze": false,
   "back": 0.5,
   "negative_fill": false
  },
  "grade": {
   "sat": 1.25,
   "light": 1.0,
   "contrast": "punchy contrast, saturated mids",
   "shift": 0
  },
  "transition": "whip-pan and match cuts",
  "rhythm": "fast; cut on motion",
  "complexity": 3
 },
 "suspense": {
  "name": "Suspenseful Low-Key",
  "objective": "Withhold information: let darkness and slow movement make the audience lean forward.",
  "emotion": "uneasy, suspenseful, dread",
  "keywords": [
   "horror",
   "thriller",
   "mystery",
   "suspense",
   "fear",
   "dread",
   "night",
   "unease",
   "creepy",
   "tension",
   "anxiety",
   "secret"
  ],
  "treatment": "balanced",
  "movement": 1,
  "lighting": 3,
  "min_budget": "low",
  "size_step": 1,
  "angle": "high angle",
  "composition": "over-the-shoulder with foreground occlusion",
  "lens_ff": [
   35,
   50
  ],
  "aperture": "T2–T2.8",
  "dof": "shallow with a slow rack focus to reveal",
  "moves": [
   "creeping dolly-in",
   "static"
  ],
  "speed": 0.05,
  "light": {
   "style": "low-key top light and cool moonlight against warm practicals",
   "ratio": 6,
   "key_az": 15,
   "key_el": 65,
   "softness": 0.4,
   "haze": true,
   "back": 0.5,
   "negative_fill": true
  },
  "grade": {
   "sat": 0.65,
   "light": 0.75,
   "contrast": "deep shadows with detail, cool shadows / warm practicals",
   "shift": -12
  },
  "transition": "long holds, delayed cuts",
  "rhythm": "slow; cut later than comfortable",
  "complexity": 3
 },
 "stylized": {
  "name": "Stylized Colour & Silhouette",
  "objective": "Turn the scene into a graphic image: shape and colour carry the emotion.",
  "emotion": "bold, iconic, heightened",
  "keywords": [
   "music video",
   "commercial",
   "fashion",
   "stylized",
   "stylised",
   "neon",
   "graphic",
   "art",
   "surreal",
   "fantasy",
   "sci-fi",
   "iconic"
  ],
  "treatment": "stylized",
  "movement": 1,
  "lighting": 4,
  "min_budget": "medium",
  "size_step": 2,
  "angle": "eye level",
  "composition": "centred profile silhouettes",
  "lens_ff": [
   24,
   35
  ],
  "aperture": "T4–T5.6",
  "dof": "deep — crisp graphic edges",
  "moves": [
   "static",
   "precise lateral slide"
  ],
  "speed": 0.2,
  "light": {
   "style": "coloured backlight silhouette with complementary edge gels",
   "ratio": 16,
   "key_az": 170,
   "key_el": 15,
   "softness": 0.5,
   "haze": true,
   "back": 1.0,
   "negative_fill": true
  },
  "grade": {
   "sat": 1.45,
   "light": 0.9,
   "contrast": "graphic, near-black shadows, saturated gels",
   "shift": 0
  },
  "transition": "graphic match cuts",
  "rhythm": "rhythmic, cut to beat",
  "complexity": 4
 },
 "observational": {
  "name": "Intimate Observational",
  "objective": "Watch from a respectful distance through the world, as if the camera shouldn't be there.",
  "emotion": "voyeuristic, private, contemplative",
  "keywords": [
   "documentary",
   "drama",
   "intimate",
   "private",
   "observational",
   "lonely",
   "isolation",
   "contemplative",
   "quiet",
   "secret",
   "realism",
   "indie"
  ],
  "treatment": "realistic",
  "movement": 0,
  "lighting": 0,
  "min_budget": "micro",
  "size_step": -1,
  "angle": "eye level",
  "composition": "through foreground elements, profile",
  "lens_ff": [
   100,
   180
  ],
  "aperture": "T2.8",
  "dof": "shallow, focus searches between subjects",
  "moves": [
   "static on sticks",
   "subtle reframe pan"
  ],
  "speed": 0.05,
  "light": {
   "style": "available / existing light, gently augmented",
   "ratio": 3,
   "key_az": 60,
   "key_el": 20,
   "softness": 0.85,
   "haze": false,
   "back": 0.2,
   "negative_fill": false
  },
  "grade": {
   "sat": 0.9,
   "light": 1.0,
   "contrast": "soft, filmic",
   "shift": 4
  },
  "transition": "cuts on looks and reactions",
  "rhythm": "patient, observational",
  "complexity": 1
 },
 "formal": {
  "name": "Composed Formalism",
  "objective": "Use symmetry and precise geometry so every frame feels authored and deliberate.",
  "emotion": "controlled, ironic, precise",
  "keywords": [
   "comedy",
   "satire",
   "period",
   "drama",
   "formal",
   "symmetry",
   "order",
   "control",
   "quirky",
   "deadpan",
   "whimsical"
  ],
  "treatment": "stylized",
  "movement": 1,
  "lighting": 2,
  "min_budget": "low",
  "size_step": 1,
  "angle": "eye level",
  "composition": "dead-centre frontal symmetry",
  "lens_ff": [
   25,
   32
  ],
  "aperture": "T4–T5.6",
  "dof": "deep — everything in the set is readable",
  "moves": [
   "static",
   "precise lateral dolly",
   "whip pan between set-ups"
  ],
  "speed": 0.4,
  "light": {
   "style": "even, high-key frontal light",
   "ratio": 1.6,
   "key_az": 10,
   "key_el": 25,
   "softness": 0.9,
   "haze": false,
   "back": 0.3,
   "negative_fill": false
  },
  "grade": {
   "sat": 1.2,
   "light": 1.08,
   "contrast": "clean, bright, bold primaries",
   "shift": 0
  },
  "transition": "symmetrical cuts and whip pans",
  "rhythm": "precise, comic timing",
  "complexity": 3
 }
};
export const BUDGET_ORDER = ["micro", "low", "medium", "high"];
export const FIXTURES: Record<string, Record<string, string>> = {
 "micro": {
  "key_soft": "Bi-colour LED panel through a shower-curtain diffusion",
  "key_hard": "Bare LED / work light",
  "fill": "5-in-1 reflector (white side)",
  "back": "Small LED with barn doors",
  "neg": "Black foam board / duvetyne",
  "practical": "Household bulb (dimmable)",
  "sun": "Sun",
  "moon": "LED panel gelled ½ CTB, high and far",
  "haze": "Water-based hazer (rental) or incense in small rooms",
  "window": "Existing window + bedsheet diffusion"
 },
 "low": {
  "key_soft": "300 W COB LED + 90 cm softbox",
  "key_hard": "300 W COB LED with reflector, no diffusion",
  "fill": "Bounce board or 4×4 poly",
  "back": "LED tube or fresnel with barn doors",
  "neg": "4×4 floppy flag",
  "practical": "Tunable practical bulbs",
  "sun": "Sun + 4×4 silk",
  "moon": "COB LED gelled to 4300 K, high stand",
  "haze": "Water-based hazer",
  "window": "Window with 4×4 frame of diffusion"
 },
 "medium": {
  "key_soft": "600 W COB / 1.2 kW HMI through 8×8 diffusion",
  "key_hard": "1.2 kW fresnel / HMI",
  "fill": "8×8 ultrabounce",
  "back": "LED panel (S60 class) or 650 W fresnel",
  "neg": "8×8 solid",
  "practical": "Tunable practicals + LED tape",
  "sun": "Sun + 12×12 overhead silk",
  "moon": "Large soft LED on a high roller, 4300 K",
  "haze": "Professional hazer",
  "window": "HMI through window with diffusion frame"
 },
 "high": {
  "key_soft": "18 kW HMI or large LED array through 12×12 grid cloth",
  "key_hard": "18 kW HMI / 5 kW fresnel",
  "fill": "12×12 ultrabounce",
  "back": "SkyPanel S360 class / 2.5 kW HMI",
  "neg": "12×12 solid / floppy wall",
  "practical": "DMX-controlled practicals",
  "sun": "Sun + 20×20 silk and condor fill",
  "moon": "Balloon or condor-mounted soft LED",
  "haze": "Professional hazer with fans",
  "window": "18 kW HMI through window on condor"
 }
};
export const RIGS: Record<string, Record<string, string>> = {
 "micro": {
  "dolly": "wheelchair / skateboard dolly",
  "slider": "60 cm slider",
  "crane": "monopod raise (limited)",
  "gimbal": "handheld (gimbal if available)",
  "track": "walk-and-talk handheld",
  "tripod": "tripod"
 },
 "low": {
  "dolly": "doorway dolly",
  "slider": "1 m slider",
  "crane": "jib arm (short)",
  "gimbal": "3-axis gimbal",
  "track": "gimbal follow",
  "tripod": "fluid-head tripod"
 },
 "medium": {
  "dolly": "dolly on track",
  "slider": "motorised slider",
  "crane": "jib / small crane",
  "gimbal": "gimbal on easyrig",
  "track": "dolly on track or gimbal",
  "tripod": "fluid-head tripod"
 },
 "high": {
  "dolly": "studio dolly on track",
  "slider": "motion-control slider",
  "crane": "telescopic crane",
  "gimbal": "stabilised remote head",
  "track": "car/tracking vehicle or Steadicam",
  "tripod": "geared head"
 }
};
export const SENSORS: Record<string, any> = {"full_frame": {"w": 36.0, "h": 20.25, "crop": 1.0, "label": "Full frame"}, "super35": {"w": 24.89, "h": 14.0, "crop": 1.45, "label": "Super 35"}, "micro_four_thirds": {"w": 17.3, "h": 9.73, "crop": 2.0, "label": "Micro Four Thirds"}, "phone": {"w": 6.4, "h": 3.6, "crop": 5.6, "label": "Phone main camera"}};
export const SHOT_SIZES: string[] = ["extreme close-up", "close-up", "medium close-up", "medium", "medium wide", "full", "wide", "extreme wide"];
export const FRAMING_HEIGHT_M: Record<string, number> = {"extreme close-up": 0.14, "close-up": 0.32, "medium close-up": 0.55, "medium": 0.9, "medium wide": 1.35, "full": 2.1, "wide": 4.0, "extreme wide": 11.0};
export const SUBJECT_REF_HEIGHT: Record<string, number> = {"extreme close-up": 1.62, "close-up": 1.58, "medium close-up": 1.5, "medium": 1.35, "medium wide": 1.15, "full": 0.95, "wide": 0.95, "extreme wide": 0.95};
export const ANGLES: Record<string, [number, number]> = {"ground level": [0.15, 18], "low angle": [0.75, 12], "eye level": [1.6, 0], "high angle": [2.6, -18], "overhead": [4.5, -80], "dutch angle": [1.55, 0]};
