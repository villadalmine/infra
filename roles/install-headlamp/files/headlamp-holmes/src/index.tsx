import {
  DetailsViewSectionProps,
  registerDetailsViewSection,
} from '@kinvolk/headlamp-plugin/lib';
import { SectionBox } from '@kinvolk/headlamp-plugin/lib/CommonComponents';
import React from 'react';
import { Box, Button, CircularProgress, Typography } from '@mui/material';

const LITELLM_URL = 'http://192.168.178.90:4000/v1/chat/completions';
const LITELLM_MODEL = 'local-fast';

const SYSTEM_PROMPT = `You are an expert Kubernetes troubleshooter similar to HolmesGPT.
Analyze the Kubernetes resource JSON provided and identify:
1. The root cause of any issues
2. Specific remediation steps
3. Any related resources that may be involved

Be concise and actionable. Format your response with clear sections.`;

function isUnhealthy(resource: any): boolean {
  const phase = resource?.status?.phase;
  const containerStatuses = resource?.status?.containerStatuses || [];
  const conditions = resource?.status?.conditions || [];

  if (phase && !['Running', 'Succeeded'].includes(phase)) return true;
  for (const cs of containerStatuses) {
    if (cs.restartCount > 3) return true;
    if (cs.state?.waiting?.reason === 'CrashLoopBackOff') return true;
    if (cs.state?.waiting?.reason === 'OOMKilled') return true;
    if (!cs.ready && cs.state?.waiting) return true;
  }
  for (const cond of conditions) {
    if (cond.type === 'Ready' && cond.status === 'False') return true;
  }
  return false;
}

function DiagnosePanel({ resource }: DetailsViewSectionProps) {
  const [diagnosis, setDiagnosis] = React.useState<string>('');
  const [loading, setLoading]     = React.useState(false);
  const [error, setError]         = React.useState<string>('');
  const [ran, setRan]             = React.useState(false);

  const unhealthy = isUnhealthy(resource);

  async function runDiagnosis() {
    setLoading(true); setError(''); setDiagnosis(''); setRan(true);
    const resourceJson = JSON.stringify(
      { kind: resource.kind, metadata: resource.metadata,
        spec: (resource as any).spec, status: (resource as any).status },
      null, 2
    );
    const userMessage =
      `Investigate why ${resource.kind} "${resource.metadata?.name}" ` +
      `in namespace "${resource.metadata?.namespace}" is unhealthy.\n\n` +
      `Resource JSON:\n\`\`\`json\n${resourceJson}\n\`\`\``;
    try {
      const resp = await fetch(LITELLM_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer sk-dummy' },
        body: JSON.stringify({
          model: LITELLM_MODEL,
          messages: [
            { role: 'system', content: SYSTEM_PROMPT },
            { role: 'user',   content: userMessage },
          ],
          max_tokens: 1024, stream: false,
        }),
      });
      if (!resp.ok) throw new Error(`API error ${resp.status}: ${await resp.text()}`);
      const data = await resp.json();
      const content = data?.choices?.[0]?.message?.content;
      if (content) setDiagnosis(content);
      else throw new Error('Empty response from AI');
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => { if (unhealthy && !ran) runDiagnosis(); }, []);

  return (
    <SectionBox title="AI Diagnosis (LiteLLM)">
      <Box p={2}>
        {(!ran || (!loading && !diagnosis && !error)) && (
          <Button variant="contained" color="primary" onClick={runDiagnosis}>
            Diagnose with AI
          </Button>
        )}
        {loading && <CircularProgress size={24} />}
        {error && <Typography color="error">{error}</Typography>}
        {diagnosis && (
          <Box
            sx={{
              backgroundColor: '#f5f5f5',
              padding: 2,
              borderRadius: 1,
              whiteSpace: 'pre-wrap',
              fontFamily: 'monospace'
            }}
          >
            {diagnosis}
          </Box>
        )}
      </Box>
    </SectionBox>
  );
}

const SUPPORTED_KINDS = ['Pod','Deployment','StatefulSet','DaemonSet','Job','CronJob'];

registerDetailsViewSection(({ resource }: DetailsViewSectionProps) => {
  if (!resource || !SUPPORTED_KINDS.includes(resource.kind)) return null;
  return <DiagnosePanel resource={resource} />;
});
