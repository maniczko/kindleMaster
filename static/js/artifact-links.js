    (function () {
      function normalizeObject(value) {
        return value && typeof value === "object" && !Array.isArray(value) ? value : null;
      }

      function artifactShellUrl(payload, key) {
        const source = normalizeObject(payload) || {};
        const candidates = [
          source.artifacts,
          source.quality_state && source.quality_state.artifacts,
          source.conversion && source.conversion.artifacts,
        ];
        for (const candidate of candidates) {
          const artifacts = normalizeObject(candidate);
          if (!artifacts) continue;
          const artifact = normalizeObject(artifacts[key]);
          if (!artifact) continue;
          const artifactType = artifact.artifact_type || artifact.artifactType || "";
          if ((key === "chess_reader" || key === "chess_pgn_html") && artifactType !== "final_pdf_two_crop_reader") {
            return "";
          }
          const jobId = source.job_id || source.jobId || artifact.job_id || artifact.jobId || "";
          if (jobId) {
            return `/convert/artifact/${encodeURIComponent(jobId)}/${encodeURIComponent(key)}`;
          }
          return artifact.download_url || artifact.downloadUrl || "";
        }
        return "";
      }

      window.KindleMasterArtifactLinks = {
        artifactShellUrl,
      };
    })();
