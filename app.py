import os
import json
import urllib.parse
from flask import Flask, render_template, request
from google import genai
from google.genai import types

app = Flask(__name__)

# Считываем ключ из переменной окружения GEMINI_API_KEY
api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

# Системные инструкции для получения строгого JSON от модели
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
            error = "API ключ Gemini не настроен. Пожалуйста, добавьте GEMINI_API_KEY в переменные окружения (Environment) на Render."
        else:
            try:
                # Запрос к нейросети Gemini
                response = client.models.generate_content(
                    model='gemini-3.5-flash',
                    contents=f"Generate a 5-song playlist for this mood/vibe: {user_prompt}",
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        temperature=0.7,
                        response_mime_type="application/json"
                    )
                )

                # Разбор полученного JSON-ответа
                raw_data = json.loads(response.text)

                # Формирование ссылок для поиска в Spotify и YouTube
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
