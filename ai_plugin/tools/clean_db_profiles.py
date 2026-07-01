import sqlite3
import json
import re
import os
import string

# 取得相对于当前执行路径的绝对路径（假定在 ai_plugin 下执行）
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "embeddings.db"))

if not os.path.exists(DB_PATH):
    print(f"找不到数据库！尝试路径: {DB_PATH}")
    exit(1)

def is_valid_phrase(phrase: str) -> bool:
    # 过滤单字
    if len(phrase) <= 1:
        return False
    # 停用词开头
    stopwords_prefix = ("你", "我", "他", "她", "它", "的", "了", "啊", "吧", "呢呢", "吗", "哈", "是", "在", "就")
    if any(phrase.startswith(w) for w in stopwords_prefix):
        return False
    # 含有标点和空格
    punctuations = set(string.punctuation + " 、，。！？；：“”‘’（）《》【】\n\r\t")
    if any(p in phrase for p in punctuations):
        return False
    # 纯英文无意义组合或数字
    if phrase.isnumeric():
        return False
    return True

def clean_database():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, likes, hobbies, traits, catchphrases FROM user_profiles")
    rows = cur.fetchall()
    
    cleaned_count = 0
    clean_details = []

    for row in rows:
        user_id = row[0]
        likes = json.loads(row[1])
        hobbies = json.loads(row[2])
        traits = json.loads(row[3])
        catchphrases = json.loads(row[4])
        
        orig_catchphrases = list(catchphrases)
        
        # 清洗
        catchphrases = [p for p in catchphrases if is_valid_phrase(p)]
        # 去重
        catchphrases = list(set(catchphrases))
        
        if orig_catchphrases != catchphrases:
            cleaned_count += 1
            clean_details.append({
                "user_id": user_id,
                "removed": list(set(orig_catchphrases) - set(catchphrases))
            })
            # 更新回数据库
            cur.execute("""
                UPDATE user_profiles 
                SET catchphrases = ? 
                WHERE user_id = ?
            """, (json.dumps(catchphrases, ensure_ascii=False), user_id))
    
    conn.commit()
    conn.close()
    
    print(f"✅ 清洗完毕！共清理了 {cleaned_count} 个被污染的用户档案。")
    if clean_details:
        print("\n=== 清理详情 ===")
        for d in clean_details:
            print(f"用户ID: {d['user_id']} | 抹除无意义口头禅: {d['removed']}")

if __name__ == "__main__":
    clean_database()
