import { useEffect, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin, { type Region } from "wavesurfer.js/dist/plugins/regions.esm.js";
import { apiRequest, apiUrl } from "./api";

export type TimelineSegment = {
  id: string;
  start_time: number;
  end_time: number;
};

type WaveformResponse = {
  duration: number;
  peaks: number[];
};

type Props = {
  videoId: string;
  fileUrl: string;
  duration: number;
  media: HTMLVideoElement | null;
  segments: TimelineSegment[];
  selectedSegmentId: string;
  onRangeChange: (id: string, start: number, end: number) => void;
  onSelect: (id: string) => void;
  onError: (message: string) => void;
};

export default function WaveformTimeline({
  videoId,
  fileUrl,
  duration,
  media,
  segments,
  selectedSegmentId,
  onRangeChange,
  onSelect,
  onError,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const waveRef = useRef<WaveSurfer | null>(null);
  const regionsRef = useRef<RegionsPlugin | null>(null);
  const syncingRef = useRef(false);
  const readyRef = useRef(false);
  const syncRegionsRef = useRef<() => void>(() => undefined);
  const onRangeChangeRef = useRef(onRangeChange);
  const onSelectRef = useRef(onSelect);
  const zoomRef = useRef(35);
  const [zoom, setZoom] = useState(35);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    onRangeChangeRef.current = onRangeChange;
    onSelectRef.current = onSelect;
  }, [onRangeChange, onSelect]);
  zoomRef.current = zoom;

  useEffect(() => {
    if (!containerRef.current || !media) return;
    let active = true;
    const regions = RegionsPlugin.create();
    const wave = WaveSurfer.create({
      container: containerRef.current,
      media,
      height: 110,
      minPxPerSec: zoom,
      waveColor: "#4b6478",
      progressColor: "#67e8c1",
      cursorColor: "#f8fafc",
      cursorWidth: 2,
      dragToSeek: true,
      autoScroll: true,
      plugins: [regions],
    });
    waveRef.current = wave;
    regionsRef.current = regions;
    readyRef.current = false;
    setLoading(true);
    onError("");

    regions.on("region-updated", (region: Region) => {
      if (!syncingRef.current) onRangeChangeRef.current(region.id, region.start, region.end);
    });
    regions.on("region-clicked", (region: Region, event: MouseEvent) => {
      event.stopPropagation();
      onSelectRef.current(region.id);
    });
    wave.on("ready", () => {
      if (active) {
        readyRef.current = true;
        setLoading(false);
        syncRegionsRef.current();
        wave.zoom(zoomRef.current);
      }
    });
    wave.on("error", (error: Error) => {
      if (active) {
        setLoading(false);
        onError(`Waveform preview failed: ${error.message}`);
      }
    });

    apiRequest<WaveformResponse>(`/api/videos/${videoId}/waveform`)
      .then((result) => {
        if (!active) return;
        return wave.load(
          apiUrl(fileUrl),
          [Float32Array.from(result.peaks.flatMap((peak) => [-peak, peak]))],
          result.duration || duration,
        );
      })
      .catch((reason: unknown) => {
        if (active) {
          setLoading(false);
          onError(reason instanceof Error ? reason.message : "Could not load waveform.");
        }
      });

    return () => {
      active = false;
      readyRef.current = false;
      wave.destroy();
      waveRef.current = null;
      regionsRef.current = null;
    };
  }, [videoId, fileUrl, duration, media]);

  useEffect(() => {
    const regions = regionsRef.current;
    if (!regions) return;
    const sync = () => {
      if (!readyRef.current) return;
      syncingRef.current = true;
      regions.clearRegions();
      for (const segment of segments) {
        regions.addRegion({
          id: segment.id,
          start: segment.start_time,
          end: segment.end_time,
          drag: true,
          resize: true,
          color: segment.id === selectedSegmentId ? "#67e8c155" : "#38bdf855",
          content: segment.id === selectedSegmentId ? "Selected clip" : "Clip segment",
        });
      }
      window.requestAnimationFrame(() => { syncingRef.current = false; });
    };
    syncRegionsRef.current = sync;
    sync();
  }, [segments, selectedSegmentId]);

  useEffect(() => {
    if (readyRef.current) waveRef.current?.zoom(zoom);
  }, [zoom]);

  return (
    <div className="timeline-wrap">
      <div className="timeline-toolbar">
        <span className="muted small">{loading ? "Building local waveform…" : "Drag clip handles to adjust in/out"}</span>
        <div className="timeline-zoom">
          <button type="button" className="secondary-button" aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(10, value - 10))}>−</button>
          <span className="muted small">Zoom</span>
          <button type="button" className="secondary-button" aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(180, value + 10))}>+</button>
        </div>
      </div>
      <div className="waveform-scroll"><div className="waveform-canvas" ref={containerRef} /></div>
    </div>
  );
}
