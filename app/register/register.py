import os
import requests
from supabase import create_client, Client

# Supabaseの接続情報（実際の値に書き換えてください）
# 本番運用時は環境変数（os.environ）から取得することを推奨します
SUPABASE_URL = "https://zljswppciglhvwjyquow.supabase.co"
SUPABASE_KEY = "sb_publishable_Ebl1Rfth5d2_Kikh6nK2wA_PEM75NnJ"

# Supabaseクライアントの初期化
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# 9784875932673
def get_book_info_openbd(isbn,shelf_level):
    """openBD APIから書籍情報を取得する"""
    url = f"https://api.openbd.jp/v1/get?isbn={isbn}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        if data and data[0] is not None:
            summary = data[0].get("summary", {})
            title = summary.get("title", "タイトル不明")
            author = summary.get("author", "著者不明")

            #publisher = summary.get("publisher", "出版社不明")
            #pubdate = summary.get("pubdate", "出版年不明")
            #tags = summary.get("tags", "タグ不明")9784875932673

            
            onix = data[0].get("onix", {})
            tags = ""
            try:
                collateral_detail = onix.get("CollateralDetail", {})
                text_content = collateral_detail.get("TextContent", [])
                if text_content:
                    tags = text_content[0].get("Text", "")
            except Exception:
                pass

            # Supabaseのテーブルのカラム名に合わせた辞書データを作成
            return {
                "isbn": isbn,
                "title": title,
                "author": author,
                "status": "available",
                "shelf_level": shelf_level
                #"publisher": publisher,
                #"pubdate": pubdate,
                

            }
        else:
            print("openBDに該当する書籍が見つかりませんでした。")
            return None
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        return None

def save_to_supabase(book):
    """書籍情報をSupabaseに挿入する"""
    try:
        # 'books' テーブルにデータを挿入
        # .upsert() を使うと、すでに同じISBNがある場合に上書き（または無視）できます。
        # 単に挿入するだけなら .insert() でも構いません。
        # 重複エラーを防ぐために，on_conflict="isbn"を追加
        response = supabase.table("books").upsert(book).execute()
        
        print(f"「{book['title']}」をSupabaseに登録しました！\n")
    except Exception as e:
        print(f"Supabaseへの登録中にエラーが発生しました: {e}\n")

def main():
    print("=== openBD + Supabase 書籍自動登録システム ===")
    print("終了するには 'q' を入力してください。\n")

    while True:
        isbn = input("バーコードをスキャン（またはISBNを入力）: ").strip()
        isbn = isbn.replace("-", "")

        if isbn.lower() == "q":
            print("プログラムを終了します。")
            break

        if not (isbn.isdigit() and len(isbn) in [10, 13]):
            print("無効な入力です。正しいISBNを入力してください。")
            continue
        
        # 棚の段数を入力
        shelf_input = input("この本を置く棚の段数（数字）を入力してください： ").strip()

        if not shelf_input.isdigit():
            print("エラー：棚の段数は半角数字で入力してください。最初からやり直します。\n")
            continue
        shelf_level = int(shelf_input)
        
        print(f"ISBN: {isbn} をopenBDで検索中...")
        book_info = get_book_info_openbd(isbn,shelf_level)

        if book_info:
            # CSVの代わりにSupabaseに保存
            save_to_supabase(book_info)

if __name__ == "__main__":
    main()