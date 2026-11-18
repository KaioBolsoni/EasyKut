from database.db_manager import init_db, add_video, list_videos

init_db()
vid_id = add_video("https://www.youtube.com/watch?v=teste123")
print(f"Video inserido com ID: {vid_id}")
print("Videos no banco:", list_videos())
