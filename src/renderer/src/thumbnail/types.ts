// preload 의 썸네일 API 타입에서 끌어온다 (렌더러 tsconfig 는 preload 파일을 직접 import 하지 못한다)
type Api = Window['electronAPI']['thumbnail']
type Ok<T> = Extract<T, { ok: true }> extends { data: infer D } ? D : never

export type ThumbFolderInfo = Ok<Awaited<ReturnType<Api['load']>>>
export type ThumbRunResult = Ok<Awaited<ReturnType<Api['generate']>>>
export type ThumbItem = ThumbRunResult['items'][number]
export type ThumbText = ThumbItem['text']
export type ThumbPosition = ThumbText['position']
export type ThumbColor = ThumbText['color']
export type ThumbFont = ThumbText['font']
export type ThumbPatch = Parameters<Api['compose']>[2]
