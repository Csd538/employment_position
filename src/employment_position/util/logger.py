import asyncio
import json
import logging
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

# debug模式
DEBUG = False

# 项目根目录下的默认日志目录。
DEFAULT_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"


class StructuredLogFormatter(logging.Formatter):
    """结构化JSON日志格式化器"""

    def __init__(self, system: str = "default_system", stage: str = "default_stage"):
        super().__init__()
        self.system = system
        self.stage = stage

    def format(self, record: logging.LogRecord) -> str:
        # 提取事件类型和堆栈信息
        event = getattr(record, "event", "unknown_event")
        stack_trace = getattr(record, "stack_trace", None)

        if record.levelno == logging.ERROR and record.exc_info:
            exc_type, _, exc_tb = record.exc_info
            event = exc_type.__name__
            stack_trace = self._format_exception(exc_tb)

        # 构建日志结构体
        log_data = {
            "thread_id": record.thread,
            "thread_name": record.threadName,
            "task_name": self._current_task_name(),
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "system": getattr(record, "system", self.system),
            "stage": getattr(record, "stage", self.stage),
            "event": event,
            "original_args": getattr(record, "original_args", None),
            "message": record.getMessage(),
            "caller": {
                "module": record.module,
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            },
            "duration_ms": getattr(record, "duration_ms", None),
            "stack_trace": stack_trace,
        }

        return json.dumps(
            {key: value for key, value in log_data.items() if value is not None},
            ensure_ascii=False,
            default=self._json_serializer,
        )

    @staticmethod
    def _json_serializer(obj):
        """JSON序列化钩子函数"""
        if isinstance(obj, datetime | timedelta):
            return str(obj)
        if hasattr(obj, "__dict__"):
            return vars(obj)
        return f"<不可序列化对象: {type(obj).__name__}>"

    @staticmethod
    def _format_exception(tb):
        """格式化异常堆栈"""
        return "".join(traceback.format_tb(tb))

    @staticmethod
    def _current_task_name() -> str | None:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            return None
        return task.get_name() if task else None


def setup_logger(
    system: str = "default_system",
    stage: str = "default_stage",
    log_file_path: str | Path | None = None,
) -> logging.Logger:
    """创建同时输出到控制台和日志文件的日志记录器。"""
    logger = logging.getLogger(f"{system}.{stage}")
    logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = StructuredLogFormatter(system=system, stage=stage)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    log_path = (
        Path(log_file_path)
        if log_file_path
        else DEFAULT_LOG_DIR / f"{stage}.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger