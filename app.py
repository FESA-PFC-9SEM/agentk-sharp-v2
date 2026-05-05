import json
import streamlit as st
from agent import chat
from k8s_tools import list_resources, get_resource_yaml
from workflow.analysis import AnalysisReport, analyze_manifest, apply_selected, add_to_skip
from workflow.classification import category_meta, group_findings

st.set_page_config(page_title="K8s Assistant", page_icon="☸", layout="wide")
st.title("☸ Kubernetes Configuration Assistant")
st.caption("Powered by Ollama")

tab_chat, tab_analyze = st.tabs(["💬 Chat", "🔍 Analyze Manifest"])


# ---------------------------------------------------------------------------
# Chat tab
# ---------------------------------------------------------------------------
with tab_chat:
    with st.sidebar:
        st.markdown("**Quick prompts**")
        suggestions = [
            "List all pods in default namespace",
            "Show deployments across all namespaces",
            "List all nodes",
            "Show services in kube-system",
        ]
        for s in suggestions:
            if st.button(s, use_container_width=True, key=s):
                st.session_state.pending_input = s
                st.rerun()

    pending = st.session_state.pop("pending_input", None)
    prompt = st.chat_input("Ask about your cluster...") or pending

    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                reply = chat(prompt)
            st.markdown(reply)


# ---------------------------------------------------------------------------
# Analyze tab
# ---------------------------------------------------------------------------

_CLUSTER_KINDS = ["deployment", "statefulset", "daemonset", "pod", "service", "configmap"]

with tab_analyze:
    source = st.radio("Source", ["Upload file", "Fetch from cluster"], horizontal=True)

    yaml_content = None
    filename = None

    if source == "Upload file":
        uploaded = st.file_uploader("Upload a Kubernetes manifest (YAML)", type=["yaml", "yml"])
        if uploaded:
            yaml_content = uploaded.read().decode("utf-8")
            filename = uploaded.name
            with st.expander("Preview manifest", expanded=False):
                st.code(yaml_content, language="yaml")

    else:  # Fetch from cluster
        fc_cols = st.columns([2, 2, 1])
        with fc_cols[0]:
            kind = st.selectbox("Resource kind", _CLUSTER_KINDS)
        with fc_cols[1]:
            ns = st.text_input("Namespace", value="default")
        with fc_cols[2]:
            st.markdown("<br>", unsafe_allow_html=True)
            list_clicked = st.button("List", use_container_width=True)

        if list_clicked:
            with st.spinner(f"Listing {kind}s in {ns}…"):
                raw = list_resources(kind, ns)
            data = json.loads(raw)
            if "error" in data:
                st.error(data["error"])
                st.session_state.pop("cluster_resources", None)
            else:
                st.session_state["cluster_resources"] = [i["name"] for i in data.get("items", [])]
                st.session_state["cluster_kind"] = kind
                st.session_state["cluster_ns"] = ns

        resources = st.session_state.get("cluster_resources", [])
        if resources:
            selected_name = st.selectbox(
                f"Select {st.session_state.get('cluster_kind', kind)}",
                resources,
                key="cluster_selected",
            )
            if st.button("Fetch YAML", use_container_width=False):
                with st.spinner(f"Fetching {selected_name}…"):
                    fetched, fetch_source = get_resource_yaml(
                        st.session_state.get("cluster_kind", kind),
                        selected_name,
                        st.session_state.get("cluster_ns", ns),
                    )
                if fetch_source == "error":
                    st.error(json.loads(fetched)["error"])
                else:
                    st.session_state["fetched_yaml"] = fetched
                    st.session_state["fetched_name"] = f"{selected_name}.yaml"
                    st.session_state["fetched_source"] = fetch_source

            if "fetched_yaml" in st.session_state:
                yaml_content = st.session_state["fetched_yaml"]
                filename = st.session_state["fetched_name"]
                fetch_source = st.session_state.get("fetched_source", "")
                badge = "🟢 last-applied config" if fetch_source == "last-applied" else "🟡 live state (no last-applied annotation found)"
                with st.expander(f"Preview fetched manifest — {badge}", expanded=False):
                    st.code(yaml_content, language="yaml")

    if yaml_content and filename:
        flag_cols = st.columns(2)
        with flag_cols[0]:
            run_static = st.checkbox("Run static analysis (Checkov)", value=True)
        with flag_cols[1]:
            run_semantic = st.checkbox("Run semantic review (LLM)", value=True)

        if st.button("▶ Run Analysis", type="primary"):
            with st.spinner("Running analysis…"):
                st.session_state["analysis_report"] = analyze_manifest(
                    yaml_content, filename,
                    run_static=run_static,
                    run_semantic=run_semantic,
                )
            st.session_state.pop("apply_result", None)

    report: AnalysisReport | None = st.session_state.get("analysis_report")
    active_filename = (
        st.session_state.get("fetched_name") if source == "Fetch from cluster"
        else (uploaded.name if source == "Upload file" and uploaded else None)
    )

    if report and report.filename == active_filename:

        # --- Summary ---
        col1, col2, col3, col4 = st.columns(4)
        sem_patch_count = sum(1 for sfr in report.semantic_finding_reports if sfr.patch)
        col1.metric("Checkov issues", report.total_findings)
        col2.metric("Checkov patches", report.patched_count)
        col3.metric("Semantic findings", len(report.semantic_finding_reports))
        col4.metric("Semantic patches", sem_patch_count)

        # --- Semantic review ---
        if report.semantic_finding_reports:
            st.divider()
            st.markdown("### 🧠 Semantic review")

            _SEVERITY_ICON = {
                "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪",
            }
            _CATEGORY_LABEL = {
                "typo": "Typo",
                "hardcoded-secret": "Hardcoded secret",
                "port-mismatch": "Port mismatch",
                "label-mismatch": "Label/selector mismatch",
                "resource-sizing": "Resource sizing",
                "encoding-issue": "Encoding issue",
                "coherence": "Coherence",
                "other": "Other",
            }

            for si, sfr in enumerate(report.semantic_finding_reports):
                sf = sfr.finding
                icon = _SEVERITY_ICON.get(sf.severity, "⚪")
                label = _CATEGORY_LABEL.get(sf.category, sf.category)
                patch_icon = "🔧" if sfr.patch else "💬"
                header = f"{patch_icon} {icon} **{label}** — `{sf.resource}` · `{sf.field}`"
                with st.expander(header, expanded=False):
                    st.markdown(f"**Description:** {sf.description}")
                    st.info(f"💡 {sf.suggestion}")

                    if sfr.patch:
                        st.divider()
                        pcols = st.columns([1, 2])
                        with pcols[0]:
                            st.markdown("**Generated patch**")
                            st.markdown(f"- **Action:** `{sfr.patch.action}`")
                            st.markdown(f"- **Path:** `{sfr.patch.path}`")
                            if sfr.patch.value is not None:
                                st.markdown(f"- **Value:** `{sfr.patch.value}`")
                            st.caption(sfr.patch.explanation)
                        with pcols[1]:
                            st.caption("Reasoning")
                            st.info(sfr.patch.reasoning)

                        if sfr.extra_resource:
                            st.divider()
                            st.markdown("**Generated Secret resource**")
                            st.caption("This Secret will be appended to the patched manifest when the fix is applied.")
                            st.code(sfr.extra_resource, language="yaml")

                        st.checkbox(
                            "Apply this fix",
                            value=True,
                            key=f"sem_apply_{si}",
                        )
                    else:
                        st.warning("No patch could be generated for this finding.")

        st.divider()

        # --- Per-finding cards grouped by category ---
        st.markdown("### Select fixes to apply")

        # Build a flat index map so checkbox keys stay stable across groups
        finding_index = {id(fr): i for i, fr in enumerate(report.finding_reports)}
        grouped = group_findings(report.finding_reports)

        for cat_name, frs in grouped.items():
            meta = category_meta(cat_name)
            badge_color = meta.get("badgeColor", "#444")
            text_color = meta.get("textColor", "#fff")
            st.markdown(
                f'<span style="background:{badge_color};color:{text_color};'
                f'padding:3px 10px;border-radius:4px;font-size:0.85rem;font-weight:600">'
                f'{cat_name}</span>',
                unsafe_allow_html=True,
            )

            for fr in frs:
                i = finding_index[id(fr)]
                f = fr.finding
                has_patch = fr.patch is not None
                status = "✅" if has_patch else "⚠️"
                header = f"{status} `{f.check_id}` — {f.check_name} &nbsp;·&nbsp; `{f.resource}`"

                with st.expander(header, expanded=False):
                    col_l, col_r = st.columns(2)

                    with col_l:
                        st.markdown("**Manifest chunk (checkov flagged)**")
                        st.code(f.code_block, language="yaml")

                    with col_r:
                        st.markdown("**CIS Benchmark context**")
                        if not fr.rag_results:
                            st.caption("No CIS benchmark section matched above the confidence threshold.")
                        else:
                            top = fr.rag_results[0]
                            st.markdown(
                                f"**[CIS {top.chunk.section}]** {top.chunk.title}  \n"
                                f"📄 Page {top.chunk.page} &nbsp;·&nbsp; score `{top.score:.3f}`"
                            )
                            st.caption(top.chunk.text[:500])
                            if top.chunk.references:
                                st.markdown("**References**")
                                for ref in top.chunk.references[:3]:
                                    st.markdown(f"- {ref}")

                    if has_patch:
                        st.divider()
                        pcols = st.columns([1, 2])
                        with pcols[0]:
                            st.markdown("**Generated patch**")
                            st.markdown(f"- **Action:** `{fr.patch.action}`")
                            st.markdown(f"- **Path:** `{fr.patch.path}`")
                            if fr.patch.value is not None:
                                st.markdown(f"- **Value:** `{fr.patch.value}`")
                            st.caption(fr.patch.explanation)
                        with pcols[1]:
                            st.caption("Reasoning")
                            st.info(fr.patch.reasoning)

                        st.divider()
                        action_cols = st.columns(2)
                        with action_cols[0]:
                            st.checkbox(
                                "Apply this fix",
                                value=True,
                                key=f"apply_{i}",
                            )
                        with action_cols[1]:
                            st.checkbox(
                                f"Ignore `{f.check_id}` forever",
                                value=False,
                                key=f"ignore_{i}",
                                help="Adds this check ID to skip_checks.json so it won't appear in future analyses.",
                            )
                    else:
                        st.warning("No patch could be generated for this check.")
                        st.checkbox(
                            f"Ignore `{f.check_id}` forever",
                            value=False,
                            key=f"ignore_{i}",
                            help="Adds this check ID to skip_checks.json so it won't appear in future analyses.",
                        )

        # --- Apply button ---
        st.divider()
        sem_patch_count = sum(1 for sfr in report.semantic_finding_reports if sfr.patch)
        apply_disabled = report.patched_count == 0 and sem_patch_count == 0
        if st.button("⚙ Apply selected fixes", type="primary", disabled=apply_disabled):
            selected = {
                i for i in range(len(report.finding_reports))
                if st.session_state.get(f"apply_{i}", False)
            }
            semantic_selected = {
                i for i in range(len(report.semantic_finding_reports))
                if st.session_state.get(f"sem_apply_{i}", False)
            }
            to_ignore = [
                report.finding_reports[i].finding.check_id
                for i in range(len(report.finding_reports))
                if st.session_state.get(f"ignore_{i}", False)
            ]

            patched_yaml, diff = apply_selected(report, selected, semantic_selected)
            st.session_state["apply_result"] = (patched_yaml, diff, report.filename)

            if to_ignore:
                add_to_skip(to_ignore)
                st.success(f"Added to skip_checks.json: {', '.join(to_ignore)}")

        # --- Diff + download (persists across reruns) ---
        if "apply_result" in st.session_state:
            patched_yaml, diff, orig_filename = st.session_state["apply_result"]

            st.markdown("### Unified diff")
            if diff == "(no changes)":
                st.info("No changes — no fixes were selected.")
            else:
                st.code(diff, language="diff")

            st.download_button(
                "⬇ Download patched manifest",
                data=patched_yaml,
                file_name=f"patched_{orig_filename}",
                mime="text/yaml",
            )
