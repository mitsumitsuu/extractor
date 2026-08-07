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
import time
import streamlit.components.v1 as components

# ==========================================
# UIカスタマイズ & JSインジェクション
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

# エンターキーでのフォーカス移動 ＆ オートコンプリート無効化スクリプト
js_code = f"""
<script>
    // {time.time()} (Force script re-evaluation)
    const inputs = parent.document.querySelectorAll('input:not([type="hidden"]), textarea');
    inputs.forEach(el => el.setAttribute('autocomplete', 'off'));
    
    parent.document.addEventListener('keydown', function(e) {{
        if (e.key === 'Enter') {{
            const active = parent.document.activeElement;
            const inputsArray = Array.from(inputs);
            const index = inputsArray.indexOf(active);
            if (index > -1 && index < inputsArray.length - 1) {{
                e.preventDefault();
                inputsArray[index + 1].focus();
            }}
        }}
    }});
</script>
"""
components.html(js_code, height=0, width=0)

# ==========================================
# 初期設定とデフォルト辞書
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花, GUMI, 音街ウナ"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"
VOCAL_ORDER = ["初音ミク", "鏡音リン", "鏡音レン", "MEIKO", "KAITO", "GUMI", "音街ウナ", "巡音ルカ", "星界", "可不", "重音テト", "花隈千冬", "夏色花梨", "小春六花"]

if "presets" not in st.session_state:
    st.session_state.presets = {i: {} for i in range(1, 11)}

col_title, col_link = st.columns([4, 1])
with col_title:
    st.title("🎶 楽曲抽出システム Pro (Ver 3.0)")
with col_link:
    st.write("\n")
    st.markdown("[👤 制作者 (Mitsu) の lit.link](https://lit.link/_mitsu_3_)")

# ==========================================
# データ処理関数
# ==========================================
def clean_vocalist_name(name):
    if not name: return ""
    name = re.sub(r'[\(（\[【].*?[\)）\]】]', '', name)
    version_keywords = r'\b(V\d|V4X|Append|Power|Whisper|Soft|Sweet|Solid|Natural|Dark|Light|Adult|Straight|Mellow|Cute|Cool|Lite|Spicy|Quiet|Calm)\b'
    name = re.sub(version_keywords, '', name, flags=re.IGNORECASE)
    return name.strip()

def extract_vocals_manual(title, description, keywords, ng_list):
    found_vocals = set()
    t_str = str(title) if title else ""
    d_str = str(description) if description else ""
    for kw in keywords:
        if kw in t_str: found_vocals.add(kw)
    if not any(ng in d_str for ng in ng_list):
        for kw in keywords:
            if kw in d_str: found_vocals.add(kw)
    cleaned = [clean_vocalist_name(v) for v in found_vocals if clean_vocalist_name(v)]
    return " / ".join(list(set(cleaned)))

def clean_title(raw_title):
    title = str(raw_title)
    title = re.split(r'\s*[/／]\s*', title)[0]
    title = re.sub(r'\s+[^\s]*P\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r"(?i)[\(（\[【].*?(remix|bootleg|edit|mashup|flip|vip|cover|long|short|MV|PV).*?[\)）\]】]", "", title)
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

def get_youtube_playlist_api(api_key, url, min_v, max_v, min_c, max_c):
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
            
            if min_v > 0 and views < min_v: continue
            if max_v > 0 and views > max_v: continue
            if min_c > 0 and comments < min_c: continue
            if max_c > 0 and comments > max_c: continue
            
            videos.append({"曲名": title, "概要欄データ": item["snippet"].get("description", ""), "URL": f"https://www.youtube.com/watch?v={vid}", "再生数": views, "コメント数": comments})
            
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
                    videos.append({"曲名": entry.get('title', 'Unknown'), "概要欄データ": entry.get('description', ''), "URL": vid_url, "再生数": entry.get('view_count', 0), "コメント数": 0})
            return videos
    except Exception as e:
        raise ValueError(f"解析失敗: {e}")

def create_spaced_excel(df, writer):
    """VOCAL_ORDERに従って5列間隔でシートに配置する"""
    if df.empty or "合成音声" not in df.columns: return
    
    spaced_df = pd.DataFrame()
    col_index = 0
    
    for vocal in VOCAL_ORDER:
        filtered = df[df['合成音声'].str.contains(vocal, na=False, regex=False)]
        if not filtered.empty:
            # 必要な列を抜き出し
            sub_df = filtered[['曲名', 'URL']].reset_index(drop=True)
            # 列名をボーカル名を含むものに変更して重複を防ぐ
            sub_df.columns = [f'{vocal}_曲名', f'{vocal}_URL']
            
            # 結合
            for c in sub_df.columns:
                spaced_df.insert(col_index, c, sub_df[c])
                col_index += 1
            
            # 5列の間隔をあけるため、空白列を3列追加 (曲名+URL+空白3列 = 5列周期)
            for _ in range(3):
                spaced_df.insert(col_index, f"blank_{col_index}", "")
                col_index += 1
                
    if not spaced_df.empty:
        spaced_df.to_excel(writer, sheet_name='ボーカル別配置 (5列間隔)', index=False)

# ==========================================
# プリセットレンダリング関数
# ==========================================
def render_preset(pid):
    p_state = st.session_state.presets[pid]
    
    st.header("⚙️ 1. 抽出モードとフィルター設定")
    
    mode_key = f"mode_{pid}"
    mode = st.radio("抽出・解析モードを選択", ["⚡ 高速モード (yt-dlp使用 / API不要)", "📊 統計フィルターモード (YouTube API使用)", "✨ AI完璧抽出モード (Gemini API使用 / 精度100%)"], horizontal=True, key=mode_key)
    
    col_api, col_gemini = st.columns(2)
    with col_api:
        yt_key = st.text_input("YouTube API Key (統計モード用)", key=f"yt_key_{pid}")
    with col_gemini:
        gemini_key = st.text_input("Gemini API Key (AIモード用)", key=f"gemini_key_{pid}")

    title_mode = st.radio("📝 曲名の出力モード", ["🔹 そのまま出力", "✨ スッキリ出力（【】や feat. 等を削除）"], key=f"title_mode_{pid}")

    st.markdown("---")
    
    with st.expander("🔍 詳細フィルター設定（除外設定・再生数・特定ボーカルなど）"):
        st.markdown("**【ボーカル指定フィルター】**")
        target_vocal = st.text_input("抽出する合成音声 (※指定した場合、その音声の曲のみ抽出)", key=f"target_v_{pid}", placeholder="例: 初音ミク")
        multi_vocal_only = st.checkbox("複数人が歌唱している曲のみ抽出する", key=f"multi_v_{pid}")
        
        st.markdown("**【除外設定】**")
        exclude_words = st.text_area("除外する楽曲タイトル・ワード (改行区切り)", key=f"ex_{pid}", placeholder="例:\n踊ってみた\n初音ミクの消失")
        exclude_list = [w.strip() for w in exclude_words.split('\n') if w.strip()]
        
        st.markdown("**【数値フィルター】**")
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            min_v = st.number_input("最小再生数", value=0, step=10000, format="%d", key=f"min_v_{pid}")
            max_v = st.number_input("最大再生数 (0で無制限)", value=0, step=10000, format="%d", key=f"max_v_{pid}")
        with col_v2:
            min_c = st.number_input("最小コメント数", value=0, step=100, format="%d", key=f"min_c_{pid}")
            max_c = st.number_input("最大コメント数 (0で無制限)", value=0, step=100, format="%d", key=f"max_c_{pid}")
            
        st.markdown("**【追加リンクの生成】**")
        col_link1, col_link2, col_link3 = st.columns(3)
        with col_link1:
            add_lyrics = st.checkbox("📝 歌詞検索リンクを追加", value=True, key=f"link1_{pid}")
        with col_link2:
            add_analysis = st.checkbox("🤔 考察検索リンクを追加", value=False, key=f"link2_{pid}")
        with col_link3:
            add_bpm = st.checkbox("🎛️ BPM・キー検索リンクを追加", value=False, key=f"link3_{pid}")

    st.markdown("---")
    st.header("🔗 2. URL入力と抽出開始")
    playlist_url = st.text_input("URLを入力 (YouTube / SoundCloud)", key=f"url_{pid}")

    if st.button(f"抽出開始 (プリセット {pid})", type="primary", key=f"btn_{pid}"):
        if not playlist_url.strip():
            st.warning("URLを入力してください。")
        else:
            with st.spinner("データを抽出中... (AIモードの場合は時間がかかることがあります)"):
                try:
                    raw_data = []
                    if "統計" in mode:
                        raw_data = get_youtube_playlist_api(yt_key, playlist_url.strip(), min_v, max_v, min_c, max_c)
                    else:
                        raw_data = get_playlist_ytdlp(playlist_url.strip())

                    results = []
                    keywords_list = [k.strip() for k in DEFAULT_KEYWORDS.split(',')]
                    ng_list = [n.strip() for n in DEFAULT_NG_WORDS.split(',')]

                    for item in raw_data:
                        raw_title = item["曲名"]
                        desc = item["概要欄データ"]
                        url = item["URL"]
                        
                        # 除外ワード判定
                        if any(ex in raw_title for ex in exclude_list):
                            continue
                            
                        # AIモード or 手動モード判定
                        if "AI" in mode and gemini_key:
                            clean_t, vocals = extract_vocals_ai(gemini_key, f"{raw_title}\n{desc}")
                            if not clean_t: clean_t = clean_title(raw_title)
                        else:
                            clean_t = clean_title(raw_title) if "スッキリ" in title_mode else raw_title
                            vocals = extract_vocals_manual(raw_title, desc, keywords_list, ng_list)
                            
                        # ボーカルフィルター判定
                        if target_vocal.strip() and target_vocal.strip() not in vocals:
                            continue
                        # 複数人フィルター判定
                        if multi_vocal_only and "/" not in vocals:
                            continue
                            
                        row = {"曲名": clean_t, "合成音声": vocals, "URL": url}
                        
                        # URLエンコード
                        safe_title = str(clean_t) if clean_t else "Unknown"
                        encoded_title = urllib.parse.quote(safe_title)
                        
                        if add_lyrics: row["歌詞検索"] = f"https://www.google.com/search?q={encoded_title}+歌詞"
                        if add_analysis: row["考察検索"] = f"https://www.google.com/search?q={encoded_title}+考察"
                        if add_bpm: row["BPM・キー検索"] = f"https://www.google.com/search?q={encoded_title}+BPM+Key"
                            
                        results.append(row)

                    df = pd.DataFrame(results)
                    p_state['df'] = df

                    if df.empty:
                        st.warning("条件に一致する楽曲がありませんでした。")
                    else:
                        st.success(f"✅ {len(df)}曲を抽出しました！")

                except Exception as e:
                    st.error(f"エラーが発生しました: {e}")

    # ===== 結果の表示とダウンロードUI (常に表示) =====
    if 'df' in p_state and p_state['df'] is not None and not p_state['df'].empty:
        df = p_state['df']
        st.dataframe(df, use_container_width=True)
        
        col_dl1, col_dl2, col_dl3 = st.columns([1, 1, 1])
        
        with col_dl1:
            output = BytesIO()
            # engine_kwargs でURLの自動リンク化を強制
            with pd.ExcelWriter(output, engine='xlsxwriter', engine_kwargs={'options': {'strings_to_urls': True}}) as writer:
                df.to_excel(writer, sheet_name='All Data', index=False)
                
                # 特殊配置シート作成
                create_spaced_excel(df, writer)
                
                # ボーカルごとのシート作成
                if "合成音声" in df.columns:
                    unique_vocals = df['合成音声'].dropna().unique()
                    for vocal in unique_vocals:
                        safe_vocal = re.sub(r'[\\/*?:\[\]]', '', str(vocal))
                        if safe_vocal.strip() and len(safe_vocal) < 30 and "/" not in vocal:
                            df[df['合成音声'] == vocal].to_excel(writer, sheet_name=safe_vocal[:31], index=False)
            
            st.download_button("📥 XLSX (Excel) ダウンロード", output.getvalue(), f"playlist_p{pid}.xlsx")
            
        with col_dl2:
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 CSV ダウンロード", csv, f"playlist_p{pid}.csv", "text/csv")
            
        with col_dl3:
            with st.popover("📋 結果をテキストでコピー"):
                st.write("右上のコピーアイコンを押してください")
                st.code(df.to_csv(index=False, sep='\t'), language='csv')

# ==========================================
# メイン画面 (プリセットタブの構築)
# ==========================================
preset_tabs = st.tabs([f"プリセット {i}" for i in range(1, 11)])

for i, tab in enumerate(preset_tabs):
    with tab:
        render_preset(i + 1)
