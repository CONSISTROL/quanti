"""Start the Quanti Web Console (FastAPI + built Vue frontend).

Usage:
    python run_web.py                 # serve on http://0.0.0.0:8000
    python run_web.py --port 9000     # custom port
"""
import argparse

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run Quanti Web Console')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--reload', action='store_true', help='dev auto reload')
    args = parser.parse_args()

    import uvicorn
    uvicorn.run(
        'backend.app:app',
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
