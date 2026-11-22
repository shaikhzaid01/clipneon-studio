"""
Social Media Metadata Generator for Viral Clips
Generates optimized Titles, Hooks, Descriptions, and Hashtags for:
- YouTube Shorts
- Instagram Reels
- TikTok
- Facebook Reels
"""
import re

GENERIC_HASHTAGS = ["#Shorts", "#Viral", "#Trending", "#Reels", "#ExplorePage", "#FYP", "#ForYou"]

GAMING_WORDS = {'gta', 'game', 'gaming', 'kill', 'shoot', 'car', 'cops', 'police', 'boss', 'level', 'player', 'stream', 'twitch', 'fps', 'drive', 'boat'}
PODCAST_WORDS = {'interview', 'talk', 'conversation', 'think', 'believe', 'life', 'mind', 'advice', 'money', 'business', 'success', 'story'}
COMEDY_WORDS = {'funny', 'fail', 'laugh', 'joke', 'crazy', 'lol', 'bhaag', 'arre', 'pagal', 'omg', 'bruh'}

ACTION_TITLES = [
    "Unreal Escape & High-Speed Action",
    "Wait Till You See How This Ends",
    "The Most Insane Scene Caught on Camera",
    "Top Tier Maneuver In Action",
    "Absolute Chaos Unfolds Here",
    "He Actually Pulled This Off",
    "High Octane Adrenaline Rush",
    "You Won't Believe What Happened",
    "Pure Adrenaline & Peak Action",
    "The Craziest Move Yet",
    "Insane Clutch Moment You Can't Miss",
    "Nobody Saw This Coming",
    "Maximum Velocity Chase Scene",
    "Pro Level Reaction Under Pressure"
]

ACTION_HOOKS = [
    "WAIT TILL THE END...",
    "HE ACTUALLY DID THIS...",
    "ABSOLUTE CHAOS...",
    "YOU WON'T BELIEVE THIS...",
    "HOW DID HE SURVIVE THIS...",
    "WATCH WHAT HAPPENS NEXT...",
    "LOOK AT THIS MOVE...",
    "THIS WAS UNREAL..."
]

def detect_category(transcript: str, video_title: str = '') -> str:
    combined = (transcript + ' ' + video_title).lower()
    words = set(re.findall(r'\b[a-zA-Z]+\b', combined))
    gaming_score = len(words.intersection(GAMING_WORDS))
    podcast_score = len(words.intersection(PODCAST_WORDS))
    comedy_score = len(words.intersection(COMEDY_WORDS))
    
    if gaming_score >= podcast_score and gaming_score >= comedy_score and gaming_score > 0:
        return 'gaming'
    elif podcast_score >= comedy_score and podcast_score > 0:
        return 'podcast'
    elif comedy_score > 0:
        return 'comedy'
    return 'general'

def generate_social_metadata(transcript: str, raw_title: str = '', hook: str = '', video_title: str = '', clip_index: int = 1) -> dict:
    clean_text = ' '.join(transcript.strip().split())
    category = detect_category(clean_text, video_title)
    
    title = raw_title.strip()
    is_generic = (
        not title or 
        any(p in title.lower() for p in ('clip', 'moment', 'activity', 'highlight', 'review', 'candidate', 'untitled', 'default', 'peak'))
    )

    if is_generic:
        sentences = re.split(r'[.?!।]\s*', clean_text)
        punchy = [s for s in sentences if 3 <= len(s.split()) <= 10]
        if punchy:
            title = punchy[0].strip().title()
        elif len(clean_text.split()) >= 4:
            words = clean_text.split()
            title = ' '.join(words[:7]).title()
        else:
            # For gameplay or videos without spoken dialogue
            clean_video_title = re.sub(r'\[.*?\]|\(.*?\)', '', video_title).strip()
            action_choice = ACTION_TITLES[(clip_index - 1) % len(ACTION_TITLES)]
            if clean_video_title and len(clean_video_title) > 3:
                title = f"{clean_video_title} - {action_choice}"
            else:
                title = action_choice
    
    title = re.sub(r'["\']', '', title)
    if len(title) > 65:
        title = title[:62].rsplit(' ', 1)[0] + '...'

    # 2. Hook
    hook_clean = hook.strip()
    if not hook_clean or any(p in hook_clean.lower() for p in ('clip', 'moment', 'activity', 'highlight', 'peak')):
        words = clean_text.split()
        if len(words) >= 3:
            hook_clean = ' '.join(words[:5]).upper()
        else:
            hook_clean = ACTION_HOOKS[(clip_index - 1) % len(ACTION_HOOKS)]

    # 3. Category Tags & Hashtags
    cat_tags = {
        'gaming': ["#GamingClips", "#GamerLife", "#GameplayHighlights", "#TwitchClips", "#GamingCommunity", "#EpicMoments"],
        'podcast': ["#PodcastClips", "#LifeAdvice", "#SuccessMindset", "#Inspiration", "#DeepThoughts", "#Knowledge"],
        'comedy': ["#FunnyClips", "#ComedyShorts", "#EpicFail", "#Humor", "#LaughDaily", "#Relatable"],
        'general': ["#DailyClips", "#MustWatch", "#ContentCreator", "#ViralVideo", "#Entertainment", "#WatchThis"]
    }[category]
    
    all_hashtags = [f"#{tag.lstrip('#')}" for tag in (GENERIC_HASHTAGS[:4] + cat_tags)]
    hashtags_str = ' '.join(all_hashtags)

    # 4. YouTube Shorts Metadata
    yt_description = (
        f"{title}\n\n"
        f"🔥 Watch until the end! Don't forget to Like & Subscribe for daily viral highlights.\n\n"
        f"💬 What do you think about this? Drop your thoughts in the comments below!\n\n"
        f"{hashtags_str}"
    )

    # 5. Instagram Reels Caption
    insta_caption = (
        f"{title} 😱🔥\n\n"
        f"Double tap if you didn't expect this! ❤️\n"
        f"Share this with someone who needs to see this! 🚀\n\n"
        f"• Follow for daily viral clips!\n\n"
        f"{hashtags_str}"
    )

    # 6. TikTok Caption
    tiktok_caption = f"{title} 🔥 Wait for the end... {hashtags_str}"

    # 7. Facebook Video Caption
    fb_caption = (
        f"{title}\n\n"
        f"Pure adrenaline! What would you do in this situation? Let us know in the comments! 👇\n\n"
        f"{hashtags_str}"
    )

    return {
        'title': title,
        'hook': hook_clean,
        'category': category,
        'hashtags': all_hashtags,
        'youtube_title': title[:100],
        'youtube_description': yt_description,
        'instagram_caption': insta_caption,
        'tiktok_caption': tiktok_caption,
        'facebook_caption': fb_caption
    }
