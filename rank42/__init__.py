__title__ = "Rank Hunter"
__description__ = "Computational search platform for discovering and rigorously studying high-rank elliptic curves over Q."
__repository_url__ = "https://github.com/crhillresearch/rank-hunter"
__version__ = "0.10.5"

if __import__("os").environ.get("RANK_HUNTER_REMOTE_DB_PATH"):
    from rank42.node_db_proxy import install_remote_sqlite

    install_remote_sqlite()
