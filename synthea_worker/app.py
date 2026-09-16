from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator


class GenerateRequest(BaseModel):
    population: int = Field(ge=1, le=500)
    seed: int = Field(ge=1)
    min_age: int = Field(ge=0, le=110)
    max_age: int = Field(ge=0, le=110)
    gender: str = "all"
    output_dir: str
    csv_files: list[str] = Field(default_factory=list)
    fhir_resources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_range(self) -> "GenerateRequest":
        if self.min_age > self.max_age:
            raise ValueError("最小年龄不能大于最大年龄")
        if self.gender not in {"all", "M", "F"}:
            raise ValueError("gender 仅支持 all、M 或 F")
        return self


app = FastAPI(title="Synthea internal worker")
RUNTIME_ROOT = Path(os.environ.get("RUNTIME_ROOT", "/app/runtime")).resolve()
SYNTHEA_JAR = Path(os.environ.get("SYNTHEA_JAR", "/opt/synthea/build/libs/synthea-with-dependencies.jar"))


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok" if SYNTHEA_JAR.exists() else "missing", "jar_ready": SYNTHEA_JAR.exists()}


@app.post("/generate")
async def generate(payload: GenerateRequest) -> dict[str, object]:
    output_dir = Path(payload.output_dir).resolve()
    try:
        output_dir.relative_to(RUNTIME_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="输出目录必须位于运行目录内") from exc
    if not SYNTHEA_JAR.exists():
        raise HTTPException(status_code=503, detail="Synthea JAR 尚未构建")
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "java", "-jar", str(SYNTHEA_JAR), "-s", str(payload.seed), "-p", str(payload.population),
        "-a", f"{payload.min_age}-{payload.max_age}",
        "--exporter.fhir.export=true", "--exporter.fhir.bulk_data=true", "--exporter.csv.export=true",
        f"--exporter.baseDirectory={output_dir}",
    ]
    if payload.gender != "all":
        command.extend(["-g", payload.gender])
    if payload.csv_files:
        command.append(f"--exporter.csv.included_files={','.join(payload.csv_files)}")
    if payload.fhir_resources:
        command.append(f"--exporter.fhir.included_resources={','.join(payload.fhir_resources)}")
    process = await asyncio.create_subprocess_exec(
        *command, cwd="/opt/synthea", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        text = stdout.decode("utf-8", errors="replace")[-1200:]
        raise HTTPException(status_code=500, detail=f"Synthea 执行失败：{text}")
    file_count = sum(1 for path in output_dir.rglob("*") if path.is_file())
    if file_count == 0:
        raise HTTPException(status_code=500, detail="Synthea 未产生输出文件")
    return {"status": "ok", "output_dir": str(output_dir), "file_count": file_count}
