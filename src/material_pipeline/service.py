from __future__ import annotations

import asyncio
import os
import uuid
from typing import Dict, List

from alpha.logs import logger

from src.llm_utils import SeLLM, load_config

from .extractor import FormulaExtractor
from .models import (
    FormulaExtractionResult,
    FormulaPipelineResult,
    PipelineResultResponse,
    PipelineRunAccepted,
    PipelineRunRequest,
    PipelineStatusResponse,
    PipelineTaskStatus,
)
from .runners import RunnerConfig, run_adit, run_mace, run_mp


class MaterialPipelineService:
    def __init__(self, repo_root: str):
        self.repo_root = os.path.abspath(repo_root)
        self._tasks: Dict[str, PipelineResultResponse] = {}
        self._lock = asyncio.Lock()

        cfg = load_config(os.path.join(self.repo_root, "config", "config.yaml"))
        self.llm = SeLLM(base_url=cfg["base_url_1"], api_key=cfg["api_key"])
        self.extractor = FormulaExtractor(self.llm)
        self.runner_cfg = RunnerConfig(repo_root=self.repo_root)

    async def submit(self, req: PipelineRunRequest) -> PipelineRunAccepted:
        task_id = req.task_id or str(uuid.uuid4())
        payload = PipelineResultResponse(
            task_id=task_id,
            status=PipelineTaskStatus.QUEUED,
            prompt=req.prompt,
            extracted_formulas=FormulaExtractionResult(),
            results=[],
            errors=[],
        )
        async with self._lock:
            self._tasks[task_id] = payload

        asyncio.create_task(self._run_task(task_id, req.prompt))
        return PipelineRunAccepted(task_id=task_id)

    async def get_status(self, task_id: str) -> PipelineStatusResponse:
        task = await self._get_task(task_id)
        return PipelineStatusResponse(task_id=task_id, status=task.status)

    async def get_result(self, task_id: str) -> PipelineResultResponse:
        return await self._get_task(task_id)

    async def _get_task(self, task_id: str) -> PipelineResultResponse:
        async with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    async def _set_status(self, task_id: str, status: PipelineTaskStatus):
        async with self._lock:
            self._tasks[task_id].status = status

    async def _append_error(self, task_id: str, msg: str):
        async with self._lock:
            self._tasks[task_id].errors.append(msg)

    async def _set_extraction(self, task_id: str, extracted: FormulaExtractionResult):
        async with self._lock:
            self._tasks[task_id].extracted_formulas = extracted

    async def _set_results(self, task_id: str, results: List[FormulaPipelineResult]):
        async with self._lock:
            self._tasks[task_id].results = results

    async def _run_task(self, task_id: str, prompt: str):
        await self._set_status(task_id, PipelineTaskStatus.RUNNING)
        try:
            extracted = await self.extractor.extract(prompt)
            await self._set_extraction(task_id, extracted)

            if not extracted.final_formulas:
                await self._append_error(task_id, "no valid formula extracted from prompt")
                await self._set_status(task_id, PipelineTaskStatus.FAILED)
                return

            results: List[FormulaPipelineResult] = []
            for formula in extracted.final_formulas:
                item = FormulaPipelineResult(formula=formula)

                mp = await asyncio.to_thread(run_mp, self.runner_cfg, task_id, formula)
                item.mp = mp
                if not mp.ok:
                    results.append(item)
                    await self._append_error(task_id, f"[{formula}] mp failed")
                    continue

                adit = await asyncio.to_thread(run_adit, self.runner_cfg, task_id, formula)
                item.adit = adit
                if not adit.ok:
                    results.append(item)
                    await self._append_error(task_id, f"[{formula}] adit failed")
                    continue

                mace_fast = await asyncio.to_thread(run_mace, self.runner_cfg, task_id, formula, True)
                item.mace_fast = mace_fast
                if not mace_fast.ok:
                    results.append(item)
                    await self._append_error(task_id, f"[{formula}] mace_fast failed")
                    continue

                mace_md = await asyncio.to_thread(run_mace, self.runner_cfg, task_id, formula, False)
                item.mace_md = mace_md
                if not mace_md.ok:
                    await self._append_error(task_id, f"[{formula}] mace_md failed")

                results.append(item)

            await self._set_results(task_id, results)

            has_success = any(
                r.mp and r.mp.ok and r.adit and r.adit.ok and r.mace_fast and r.mace_fast.ok
                for r in results
            )
            await self._set_status(task_id, PipelineTaskStatus.SUCCESS if has_success else PipelineTaskStatus.FAILED)
        except Exception as e:
            logger.exception(f"[MaterialPipelineService] task failed: {task_id}, err={e}")
            await self._append_error(task_id, str(e))
            await self._set_status(task_id, PipelineTaskStatus.FAILED)
