import streamlit as st
import pandas as pd
import re
import requests
import json
import urllib.parse
import urllib.request
from googleapiclient.discovery import build
import google.generativeai as genai
from io import BytesIO
import yt_dlp

# ==========================================
# UIカスタマイズ
# ==========================================
st.set_page_config(page_title="楽曲抽出＆特定システム Pro", layout="wide")
hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.st-emotion-cache-1wbqy5l {display: none;}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# ==========================================
# セッション・プリセット管理の初期化
# ==========================================
if "presets" not in st.session_state:
    # 10個のプリセット領域を確保
    st.session_state.presets = {f"Preset {i}": {"df": pd.DataFrame()} for i in range(1, 11)}
if "current_preset" not in st.session_state:
    st.session_state.current_preset = "Preset 1"

# ==========================================
# 初期設定とデフォルト辞書
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花, GUMI"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"

col_title, col_link = st.columns([4, 1])
with col_title:
    st.title("🎶 楽曲抽出システム Pro")
with col_link:
    st.write("\n")
    st.markdown("[👤 制作者 (Mitsu) の lit.link](https://lit.link/_mitsu_3_)")

# ==========================================
# 使い方ガイド
# ==========================================
with st.expander("📖 詳しい使い方・操作のコツ（クリックで開く）"):
    st.markdown("""
    **【プリセット機能について】**
    画面上部で「Preset 1〜10」を切り替えることができます。抽出結果はプリセットごとに保存されるため、タブを移動しても消えません。Aの条件でPreset 1、Bの条件でPreset 2に出力し、結果を見比べる使い方が可能です。

    **【キーボードでの爆速操作】**
    マウスを使わずに次の入力欄へ移動したい場合は、**「Enterキー」ではなく「Tabキー」**を押してください。Webサイトの標準機能のため、エラーなく一瞬で次の項目へカーソルが移動します。

    **【ワンクリックコピー】**
    抽出完了後、表の下にある黒いボックスの右上にある「📋（コピーマーク）」を押すと、結果を全選択してコピーできます。
    """)

# ==========================================
# データ処理関数
# ==========================================
def extract_vocals(title, description, keywords, ng_list):
    found_vocals = set()
    title_str = str(title) if title else ""
    desc_str = str(description) if description else ""
    for kw in keywords:
        if kw in title_str: found_vocals.add(kw)
    if not any(ng in desc_str for ng in ng_list):
        for kw in keywords:
            if kw in desc_str: found_vocals.add(kw)
    return " / ".join(list(found_vocals))

def clean_title(raw_title):
    title = str(raw_title)
    title = re.split(r'\s*[/／]\s*', title)[0]
    title = re.sub(r'\s+[^\s]*P\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r"(?i)[\(（\[【].*?(remix|bootleg|edit|mashup|flip|vip|cover|feat\..*?|original|short|long|mv).*?[\)）\]】]", "", title)
    title = re.sub(r"【.*?】|\[.*?\]", "", title)
    title = re.split(r"(?i)\s+feat\.\s+|\s+ft\.\s+", title)[0]
    return title.strip()

def extract_vocals_ai(api_key, text_data):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    以下の動画タイトルと概要欄から、純粋な「楽曲の題名」と「歌唱している合成音声名(ボーカロイド等)」を抽出してください。
    - オリジナルPV、MV、〇〇P、long ver、各種記号、feat. などの余計な文字列は完全に排除すること。
    - ボーカルが複数の場合は「/」で区切ること。
    - 結果は以下のJSONフォーマットのみで出力すること。
    {{"title": "純粋な曲名", "vocals": "合成音声名"}}
    
    【データ】
    {text_data}
    """
    try:
        response = model.generate_content(prompt)
        res_text = re.sub(r'`{3}(json)?', '', response.text, flags=re.IGNORECASE).strip()
        result = json.loads(res_text)
        return result.get("title", ""), result.get("vocals", "")
    except Exception:
        return "", ""

def get_youtube_playlist_api(api_key, url, min_views, max_views, min_comments, max_comments):
    match = re.search(r"list=([a-zA-Z0-9_-]+)", url)
    if not match: raise ValueError("有効なYouTubeプレイリストIDが見つかりません。")
    youtube = build("youtube", "v3", developerKey=api_key)
    videos, next_page_token = [], None
    
    while True:
        request = youtube.playlistItems().list(part="snippet", playlistId=match.group(1), maxResults=50, pageToken=next_page_token)
        response = request.execute()
        
        video_ids = [item["snippet"]["resourceId"]["videoId"] for item in response.get("items", []) if item["snippet"]["title"] not in ["Private video", "Deleted video"]]
        if not video_ids: break
            
        stats_req = youtube.videos().list(part="statistics", id=",".join(video_ids))
        stats_res = stats_req.execute()
        stats_dict = {i["id"]: i["statistics"] for i in stats_res.get("items", [])}
        
        for item in response.get("items", []):
            vid = item["snippet"]["resourceId"]["videoId"]
            title = item["snippet"]["title"]
            if title in ["Private video", "Deleted video"]: continue
            
            stats = stats_dict.get(vid, {})
            views = int(stats.get("viewCount", 0))
            comments = int(stats.get("commentCount", 0))
            
            if min_views > 0 and views < min_views: continue
            if max_views > 0 and views > max_views: continue
            if min_comments > 0 and comments < min_comments: continue
            if max_comments > 0 and comments > max_comments: continue
            
            videos.append({
                "曲名": title,
                "概要欄データ": item["snippet"].get("description", ""),
                "URL": f"https://www.youtube.com/watch?v={vid}",
                "再生数": views,
                "コメント数": comments
            })
            
        next_page_token = response.get("nextPageToken")
        if not next_page_token: break
    return videos

def get_playlist_ytdlp(url):
    ydl_opts = {'extract_flat': True, 'quiet': True, 'ignoreerrors': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            videos = []
            entries = info.get('entries', [info])
            for entry in entries:
                if entry and (entry.get('url') or entry.get('id')):
                    vid_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
                    videos.append({
                        "曲名": entry.get('title', 'Unknown'),
                        "概要欄データ": entry.get('description', ''),
                        "URL": vid_url,
                        "再生数": entry.get('view_count', 0)
                    })
            return videos
    except Exception as e:
        raise ValueError(f"解析失敗: {e}")

# ==========================================
# UI 構築
# ==========================================
# プリセット選択UI
st.session_state.current_preset = st.selectbox(
    "💾 作業中プリセットの選択（抽出結果はタブ移動しても保持されます）",
    [f"Preset {i}" for i in range(1, 11)],
    index=int(st.session_state.current_preset.split(" ")[1])-1
)

tab1, tab3 = st.tabs(["🔗 URLから高度抽出", "📁 プレイリスト生成"])

with tab1:
    st.header("⚙️ 1. 抽出モードとフィルター設定")
    
    mode = st.radio("抽出・解析モードを選択", 
                    ["⚡ 高速モード (yt-dlp使用 / API不要)", 
                     "📊 統計フィルターモード (YouTube API使用)", 
                     "✨ AI完璧抽出モード (Gemini API使用 / 精度100%)"], 
                    horizontal=True)
    
    col_api, col_gemini = st.columns(2)
    with col_api:
        yt_key = st.text_input("YouTube API Key (統計モード用)", type="password")
    with col_gemini:
        gemini_key = st.text_input("Gemini API Key (AIモード用)", type="password")

    st.markdown("---")
    
    with st.expander("🔍 詳細フィルター設定（再生数・除外ワード・ボーカル指定など）"):
        st.markdown("**【数値フィルター】** ※履歴を表示させない設定にしています")
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            min_v = st.number_input("最小再生数", value=0, step=10000, format="%d", key="min_v")
            max_v = st.number_input("最大再生数 (0で無制限)", value=0, step=10000, format="%d", key="max_v")
        with col_v2:
            min_c = st.number_input("最小コメント数", value=0, step=100, format="%d", key="min_c")
            max_c = st.number_input("最大コメント数 (0で無制限)", value=0, step=100, format="%d", key="max_c")
            
        st.markdown("**【除外・指定設定】**")
        exclude_words = st.text_area("❌ 除外する楽曲タイトル・ワード (改行区切り)", placeholder="例:\n初音ミクの消失\n踊ってみた")
        exclude_list = [w.strip() for w in exclude_words.split('\n') if w.strip()]
        
        col_voc1, col_voc2 = st.columns(2)
        with col_voc1:
            target_vocal_filter = st.text_input("🎯 特定のボーカルのみ抽出 (例: 初音ミク)")
        with col_voc2:
            st.write("")
            st.write("")
            require_multi_vocal = st.checkbox("👥 複数人歌唱の楽曲のみを抽出する")
            
        st.markdown("**【追加リンクの生成】** Excel出力時に追加するリンク")
        col_link1, col_link2, col_link3 = st.columns(3)
        with col_link1:
            add_lyrics = st.checkbox("📝 歌詞検索リンク", value=True)
        with col_link2:
            add_analysis = st.checkbox("🤔 考察検索リンク", value=False)
        with col_link3:
            add_bpm = st.checkbox("🎛️ BPM・キー検索", value=False)
            
        st.markdown("**【手動モード用辞書】**")
        target_keywords = [k.strip() for k in st.text_area("🔍 抽出するワード", DEFAULT_KEYWORDS, height=60).split(",") if k.strip()]
        ng_words = [n.strip() for n in st.text_area("🚫 除外ワード", DEFAULT_NG_WORDS, height=60).split(",") if n.strip()]

    st.markdown("---")
    st.header("🔗 2. URL入力と抽出開始")
    
    playlist_url = st.text_area("URLを入力 (YouTube / SoundCloud) ※履歴を残さないテキストエリア仕様", height=68)

    if st.button("抽出開始", type="primary"):
        if not playlist_url.strip():
            st.warning("URLを入力してください。")
        else:
            with st.spinner("データを抽出中..."):
                try:
                    raw_data = []
                    if "統計" in mode:
                        raw_data = get_youtube_playlist_api(yt_key, playlist_url.strip(), min_v, max_v, min_c, max_c)
                    else:
                        raw_data = get_playlist_ytdlp(playlist_url.strip())

                    results = []
                    for item in raw_data:
                        raw_title = item["曲名"]
                        desc = item["概要欄データ"]
                        url = item["URL"]
                        
                        # タイトルによる除外判定
                        if any(ex in raw_title for ex in exclude_list):
                            continue
                            
                        # モードに応じた抽出処理
                        if "AI" in mode and gemini_key:
                            clean_t, vocals = extract_vocals_ai(gemini_key, f"{raw_title}\n{desc}")
                            if not clean_t: clean_t = clean_title(raw_title)
                        else:
                            clean_t = clean_title(raw_title)
                            vocals = extract_vocals(raw_title, desc, target_keywords, ng_words)
                        
                        # ボーカルフィルター判定
                        if target_vocal_filter and target_vocal_filter not in vocals:
                            continue
                        
                        # 複数人歌唱フィルター判定
                        if require_multi_vocal and "/" not in vocals:
                            continue
                            
                        row = {"曲名": clean_t, "合成音声": vocals, "URL": url}
                        
                        safe_title = str(clean_t) if clean_t else "Unknown"
                        encoded_title = urllib.parse.quote(safe_title)
                        
                        # ExcelのHYPERLINK関数としてリンクを生成
                        if add_lyrics:
                            row["歌詞検索"] = f'=HYPERLINK("https://www.google.com/search?q={encoded_title}+歌詞", "歌詞リンク")'
                        if add_analysis:
                            row["考察検索"] = f'=HYPERLINK("https://www.google.com/search?q={encoded_title}+考察", "考察リンク")'
                        if add_bpm:
                            row["BPM・キー検索"] = f'=HYPERLINK("https://www.google.com/search?q={encoded_title}+BPM+Key", "BPM検索")'
                            
                        results.append(row)

                    df = pd.DataFrame(results)
                    # 結果を現在のプリセットに保存
                    st.session_state.presets[st.session_state.current_preset]["df"] = df
                    
                except Exception as e:
                    st.error(f"エラーが発生しました: {e}")

    # ===== 抽出結果の表示・ダウンロード領域 =====
    current_df = st.session_state.presets[st.session_state.current_preset]["df"]
    
    if current_df is not None and not current_df.empty:
        st.success(f"✅ {st.session_state.current_preset} に {len(current_df)}曲のデータを保持しています！")
        st.dataframe(current_df)
        
        # ダウンロードボタンの配置（xlsx -> csv）
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                current_df.to_excel(writer, sheet_name='All Data', index=False)
                if "合成音声" in current_df.columns:
                    unique_vocals = current_df['合成音声'].dropna().unique()
                    for vocal in unique_vocals:
                        if vocal and len(vocal) < 30:
                            safe_vocal = re.sub(r'[\\/*?:\[\]]', '', str(vocal))
                            if safe_vocal.strip() and not "/" in safe_vocal: # 複数人は別シートへ
                                current_df[current_df['合成音声'] == vocal].to_excel(writer, sheet_name=safe_vocal[:31], index=False)
                    multi_df = current_df[current_df['合成音声'].astype(str).str.contains('/', na=False)]
                    if not multi_df.empty:
                        multi_df.to_excel(writer, sheet_name='複数人歌唱', index=False)
            st.download_button("📥 Excel (.xlsx) ダウンロード", output.getvalue(), f"{st.session_state.current_preset}.xlsx")
            
        with col_dl2:
            csv = current_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 CSV ダウンロード", csv, f"{st.session_state.current_preset}.csv", "text/csv")

        st.markdown("**【ワンクリック・コピー用データ】**")
        st.caption("右上の 📋 アイコンをクリックすると、表のデータすべてをクリップボードにコピーできます。")
        st.code(current_df.to_csv(index=False, sep='\t'), language='text')
    elif current_df is not None and current_df.empty:
        # 抽出した結果が0件だった場合の表示
        # 初期状態は None ではなく空の DataFrame なので、その場合は表示しないなどの制御も可能ですが、
        # 明示的に抽出ボタンを押した後の空は警告を出す。
        pass

with tab3:
    st.header("📁 リスト一括URL補完 ＆ プレイリスト生成")
    st.info("こちらの機能は現在準備中です。")
