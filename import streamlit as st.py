import streamlit as st
import pandas as pd
import re
# YouTube API用のライブラリ（実際には pip install google-api-python-client が必要）
from googleapiclient.discovery import build

# ==========================================
# 1. 初期設定と辞書データ
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト"

st.set_page_config(page_title="プレイリスト解析ツール", layout="wide")
st.title("🎶 再生リスト抽出システム")

# ==========================================
# 2. サイドバー（設定画面）
# ==========================================
with st.sidebar:
    st.header("⚙️ 設定パネル")
    # APIキーの入力（セキュアに扱うためパスワード形式）
    api_key = st.text_input("YouTube API Key", type="password")
    
    st.subheader("抽出する合成音声名")
    st.write("※カンマ(,)区切りで自由に追加・編集できます")
    keywords_input = st.text_area("辞書リスト", DEFAULT_KEYWORDS, height=150)
    
    # 入力された文字列をリスト化し、空白を除去
    target_keywords = [k.strip() for k in keywords_input.split(",") if k.strip()]

# ==========================================
# 3. メイン画面（URL入力と実行）
# ==========================================
playlist_url = st.text_input("YouTubeの再生リストURLを入力してください")

if st.button("抽出を開始する", type="primary"):
    if not api_key or not playlist_url:
        st.warning("⚠️ APIキーと再生リストURLの両方を入力してください。")
    else:
        # URLから「list=XXXXX」の部分（プレイリストID）を正規表現で抜き出す
        playlist_id_match = re.search(r"list=([a-zA-Z0-9_-]+)", playlist_url)
        
        if playlist_id_match:
            playlist_id = playlist_id_match.group(1)
            
            with st.spinner("YouTubeからデータを取得・解析中...（数百曲の場合は時間がかかります）"):
                
                # -------------------------------------------------------------
                # 【ここにYouTube APIを呼び出し、複数ページを取得するループ処理が入ります】
                # 今回はUIの動作確認のため、ダミーデータを出力します
                # -------------------------------------------------------------
                
                # ダミーの解析結果データ
                result_data = {
                    "曲名": ["メルト", "ロキ", "マーシャル・マキシマイザー"],
                    "抽出ワード": ["初音ミク", "鏡音リン", "可不"],
                    "動画URL": [
                        f"https://youtube.com/watch?v=dummy1&list={playlist_id}",
                        f"https://youtube.com/watch?v=dummy2&list={playlist_id}",
                        f"https://youtube.com/watch?v=dummy3&list={playlist_id}"
                    ]
                }
                
                df = pd.DataFrame(result_data)
                
                st.success(f"✅ 解析が完了しました！（プレイリストID: {playlist_id}）")
                
                # テーブルとして画面に表示
                st.dataframe(df, use_container_width=True)
                
                # CSVとしてダウンロードするボタン
                csv = df.to_csv(index=False).encode('utf-8-sig') # 文字化け防止のBOM付きUTF-8
                st.download_button(
                    label="📥 結果をCSVファイルでダウンロード",
                    data=csv,
                    file_name="playlist_result.csv",
                    mime="text/csv"
                )
        else:
            st.error("❌ 有効なYouTube再生リストのURLが見つかりませんでした。")