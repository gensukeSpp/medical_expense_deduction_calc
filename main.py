def main():
    # 引数解析と初期化
    from app.args import setup_args, setup_directories

    args = setup_args()
    input_dir, output_dir, processed_dir, failed_dir = setup_directories(args)

    # データベースのテーブルを初期化するためのマイグレーション処理
    if args.db_path:
        from app.db_migrations import run_migrations
        from pathlib import Path

        schema_path = Path("docs/schema.sql")
        run_migrations(args.db_path, schema_path)

    # Web UI サーバー起動 (OCR は WatchController が遅延初期化するためここでは生成しない)
    if args.serve:
        import uvicorn
        from app.web.server import create_app

        app = create_app(
            output_dir=str(output_dir),
            db_path=args.db_path,
            input_dir=str(input_dir),
            processed_dir=str(processed_dir),
            failed_dir=str(failed_dir),
        )

        print(f"Starting Web UI at http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return

    # OCRエンジン初期化（CLI 系パスのみ）
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(use_angle_cls=True, lang="japan", enable_mkldnn=False)

    # Watcherの起動
    from app import watcher

    if args.watch:
        # Start watcher (either watchdog observer or polling loop)
        if args.use_watchdog:
            print("Starting watcher (watchdog mode)...")
            watcher.run_watchdog(
                input_dir,
                output_dir,
                processed_dir,
                failed_dir,
                poll_interval=args.poll_interval,
                retries=args.retries,
                model=args.model,
                db_path=args.db_path,
                preprocess_mode=args.preprocess_mode,
                preprocess_force=args.preprocess_force,
                target_short_side=args.target_short_side,
            )
        else:
            print("Starting watcher (polling mode)...")
            watcher.run_loop(
                input_dir,
                output_dir,
                processed_dir,
                failed_dir,
                poll_interval=args.poll_interval,
                run_once=args.run_once,
                retries=args.retries,
                model=args.model,
                db_path=args.db_path,
                preprocess_mode=args.preprocess_mode,
                preprocess_force=args.preprocess_force,
                target_short_side=args.target_short_side,
            )
        return

    # If an OCR JSON is provided, process it to structured output
    if args.input_json:
        from app.structural_parser import process_input_json

        structured = process_input_json(
            args.input_json,
            model=args.model,
            output_dir=args.output_dir,
            db_path=args.db_path,
        )
        if structured is None:
            print(f"Failed to process input JSON: {args.input_json}")
        else:
            print(f"Structured data written for {args.input_json}")
        return

    # 単一画像の処理
    from app.processor import process_single_image

    # args.image_name が存在する場合のみ process_single_image を呼び出す
    if args.image_name:
        process_single_image(
            image_name=args.image_name,
            input_dir=input_dir,
            output_dir=output_dir,
            processed_dir=processed_dir,
            model=args.model,
            db_path=args.db_path,
            ocr=ocr,
            preprocess_mode=args.preprocess_mode,
            preprocess_force=args.preprocess_force,
            target_short_side=args.target_short_side,
        )


if __name__ == "__main__":
    main()
