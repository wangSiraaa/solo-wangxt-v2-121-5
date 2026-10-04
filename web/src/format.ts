/** 数字紧凑显示：去掉多余尾零（等价于 Python f"{x:g}" 的常见用途）。 */
export function g(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return String(parseFloat(v.toPrecision(10)));
}
