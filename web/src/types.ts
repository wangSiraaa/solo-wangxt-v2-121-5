export interface CurvePoint {
  temp_c: number;
  recovered_pct: number;
}

export interface DensityRow {
  temp_c: number;
  density_g_cm3: number;
}

export interface Experiment {
  id: number;
  name: string;
  sample_id?: string | null;
  feed_density_g_cm3: number;
  residue_density_g_cm3?: number | null;
  conditions: Record<string, unknown>;
  points: CurvePoint[];
  density_rows: DensityRow[];
  notes?: string | null;
  plans?: SavedPlanSummary[];
}

export interface SavedPlanSummary {
  id: number;
  name: string;
  basis: "volume" | "mass";
  loss_pct: number;
  cuts: CutInput[];
}

export interface CutInput {
  name: string;
  start_temp_c: number;
  end_temp_c: number;
}

export interface PlanInput {
  name: string;
  basis: "volume" | "mass";
  loss_pct: number;
  cuts: CutInput[];
}

export interface Issue {
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
}

export interface CutResult extends CutInput {
  index: number;
  flags: string[];
  start_recovery_pct: number | null;
  end_recovery_pct: number | null;
  clipped_range_c: [number, number] | null;
  volume_yield_pct: number | null;
  mass_yield_pct: number | null;
}

export interface Overlap {
  cut_a: number;
  cut_b: number;
  cut_a_name: string;
  cut_b_name: string;
  from_temp_c: number;
  to_temp_c: number;
  width_c: number;
  volume_pct: number | null;
  mass_pct?: number | null;
  partial_outside_range: boolean;
  fully_outside_range: boolean;
}

export interface Gap {
  kind: "front" | "inter" | "tail";
  from_temp_c: number;
  to_temp_c: number;
  width_c: number;
  after_cut: string | null;
  before_cut: string | null;
  volume_pct: number | null;
  mass_pct?: number | null;
  partial_outside_range: boolean;
  fully_outside_range: boolean;
}

export interface MassTotals {
  nominal_yield_pct: number;
  union_yield_pct: number;
  overlap_pct: number;
  front_gap_pct: number;
  inter_gap_pct: number;
  tail_gap_pct: number;
  uncut_distillate_pct: number | null;
  light_unassigned_pct: number | null;
  residue_bottoms_pct: number | null;
  residue_bottoms_gross_pct: number | null;
  residual_total_pct: number | null;
  loss_pct: number;
  identity_sum_pct: number | null;
  identity_ok: boolean;
  identity_note: string;
  residue_density_g_cm3: number | null;
}

export interface Totals {
  nominal_yield_pct: number;
  union_yield_pct: number;
  overlap_pct: number;
  front_gap_pct: number;
  inter_gap_pct: number;
  tail_gap_pct: number;
  uncut_distillate_pct: number;
  light_unassigned_pct: number;
  residue_bottoms_pct: number;
  residue_bottoms_gross_pct: number;
  residual_total_pct: number;
  loss_pct: number;
  identity_sum_pct: number;
  identity_residual_pct: number;
  identity_ok: boolean;
  mass: MassTotals | null;
}

export interface EvalResult {
  basis: string;
  applicable_range: {
    temp_c: [number, number];
    recovered_pct: [number, number];
    extrapolation: string;
  };
  cuts: CutResult[];
  overlaps: Overlap[];
  gaps: Gap[];
  totals: Totals;
  issues: Issue[];
  has_blocking_errors: boolean;
  method: Record<string, string>;
}

export interface CurveSample {
  temps_c: number[];
  recovered_pct: number[];
  raw_points: CurvePoint[];
  range: { temp_c: [number, number]; recovered_pct: [number, number] };
  issues: Issue[];
  has_blocking_errors: boolean;
}
