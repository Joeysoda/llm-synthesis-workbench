"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useRef, useState } from "react";
import {
  listAssets,
  uploadAsset,
  type Asset,
} from "../../lib/api";

function acceptedBy(filename: string, accept: string) {
  const normalized = filename.toLowerCase();
  const extensions = accept
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.startsWith("."));
  return extensions.length === 0 || extensions.some((extension) => normalized.endsWith(extension));
}

export function AssetPicker({
  projectId,
  selected,
  onChange,
  accept,
  multiple = true,
  hint,
}: {
  projectId: string;
  selected: string[];
  onChange: (ids: string[]) => void;
  accept: string;
  multiple?: boolean;
  hint: string;
}) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);

  async function refresh() {
    if (!projectId) return;
    try {
      setAssets(await listAssets(projectId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文件读取失败");
    }
  }

  useEffect(() => {
    onChange([]);
    void refresh();
    // Clear selected assets when the project changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    const selectedFiles = Array.from(files);
    const incompatible = selectedFiles.find((file) => !acceptedBy(file.name, accept));
    if (incompatible) {
      setError(`${incompatible.name} 不适用于当前功能，请按上方格式要求选择文件`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const uploaded: Asset[] = [];
      for (const file of selectedFiles) {
        uploaded.push(await uploadAsset(projectId, file));
      }
      await refresh();
      onChange(multiple ? [...selected, ...uploaded.map((item) => item.id)] : [uploaded[0].id]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "上传失败");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="asset-picker">
      <div className="asset-actions">
        <button className="button" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "正在上传…" : "选择并上传文件"}
        </button>
        <span className="field-hint">{hint}</span>
        <input
          ref={input}
          hidden
          type="file"
          accept={accept}
          multiple={multiple}
          onChange={(event) => void upload(event.target.files)}
        />
      </div>
      {assets.length > 0 ? (
        <div className="asset-list">
          {assets.map((asset) => {
            const checked = selected.includes(asset.id);
            const compatible = acceptedBy(asset.filename, accept);
            return (
              <label
                className={[
                  "asset-row",
                  checked ? "selected" : "",
                  compatible ? "" : "incompatible",
                ]
                  .filter(Boolean)
                  .join(" ")}
                key={asset.id}
              >
                <input
                  type={multiple ? "checkbox" : "radio"}
                  checked={checked}
                  disabled={!compatible}
                  onChange={() => {
                    if (multiple) {
                      onChange(
                        checked
                          ? selected.filter((id) => id !== asset.id)
                          : [...selected, asset.id],
                      );
                    } else {
                      onChange([asset.id]);
                    }
                  }}
                />
                <span>{asset.filename}</span>
                <small>
                  {compatible
                    ? `${(asset.size / 1024).toFixed(1)} KB`
                    : "当前模式不可用"}
                </small>
              </label>
            );
          })}
        </div>
      ) : (
        <div className="empty-compact">当前项目还没有文件。</div>
      )}
      {error && <div className="form-error">{error}</div>}
    </div>
  );
}
