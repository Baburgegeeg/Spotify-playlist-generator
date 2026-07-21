import os
import json
import urllib.parse
from flask import Flask, render_template, request
from groq import Groq

app = Flask(__name__)

# Инициализируем клиент Groq
# Ключ запрашивается из переменной окружения GROQ_API_KEY
groq_api_key = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=groq_api_key) if groq_api_key else None

# Системные инструкции для выдачи строгого JSON
SYSTEM_INSTRUCTION = """
You are an expert music curator. 
Analyze the user's described mood, genre preference, or vibe and suggest 5 matching songs.

CRITICAL INSTRUCTION:
You MUST reply ONLY with a valid JSON array of objects. Do not include markdown code blocks (like ```json), do not include any intro or outro text.

Format example:
[
  {"artist": "Artist Name", "title": "Song Title", "genre": "Genre"},
  {"artist": "Artist Name 2", "title": "Song Title 2", "genre": "Genre 2"}
]
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    tracks = []
    error = None
    user_prompt = ""

    if request.method == 'POST':
        user_prompt = request.form.get('vibe', '').strip()
        
        if not user_prompt:
            error = "Пожалуйста, опишите ваше настроение или желаемый вайб."
        elif not client:
            error = "API ключ Groq не настроен. Добавьте GROQ_API_KEY в переменные окружения на Render."
        else:
            try:
                # Запрос к нейросети через Groq API
                completion = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": SYSTEM_INSTRUCTION},
                        {"role": "user", "content": f"Generate a 5-song playlist for this mood/vibe: {user_prompt}"}
                    ],
                    temperature=0.7,
                    response_format={"type": "json_object"} if hasattr(client.chat.completions, 'response_format') else None
                )

                response_content = completion.choices[0].message.content
                
                # Парсим полученный JSON
                raw_data = json.loads(response_content)

                # Если Groq обернул массив в объект вида {"songs": [...]}, извлекаем его
                if isinstance(raw_data, dict):
                    raw_data = next(iter(raw_data.values()))

                # Формируем динамические ссылки для поиска в Spotify и YouTube
                for item in raw_data:
                    query = f"{item['artist']} {item['title']}"
                    encoded_query = urllib.parse.quote(query)
                    
                    tracks.append({
                        'artist': item.get('artist', 'Unknown Artist'),
                        'title': item.get('title', 'Unknown Title'),
                        'genre': item.get('genre', 'Music'),
                        'spotify_url': f"[https://open.spotify.com/search/](https://open.spotify.com/search/){encoded_query}",
                        'youtube_url': f"[https://www.youtube.com/results?search_query=](https://www.youtube.com/results?search_query=){encoded_query}"
                    })

            except Exception as e:
                error = f"Ошибка при генерации плейлиста: {str(e)}"

    return render_template('index.html', tracks=tracks, error=error, user_prompt=user_prompt)

if __name__ == '__main__':
    app.run(debug=True)
