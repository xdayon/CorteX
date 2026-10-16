import { useSyncExternalStore } from "react";

/** Work that continues after the user leaves the page that started it (download, run creation). */
export type Task = {
  id: string;
  episodeId: string;
  message: string;
  progress: number;
  status: "running" | "failed" | "done";
  error?: string;
};

let tasks: Task[] = [];
const listeners = new Set<() => void>();

function publish(next: Task[]) {
  tasks = next;
  listeners.forEach(listener => listener());
}

export function upsertTask(task: Task) {
  publish([...tasks.filter(item => item.id !== task.id), task]);
}

export function patchTask(id: string, patch: Partial<Task>) {
  publish(tasks.map(item => item.id === id ? { ...item, ...patch } : item));
}

export function dismissTask(id: string) {
  publish(tasks.filter(item => item.id !== id));
}

export function resetTasks() { publish([]); }

export const getTasks = () => tasks;

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useTasks() {
  return useSyncExternalStore(subscribe, getTasks, getTasks);
}

/** The newest task for an episode, if any. */
export function useEpisodeTask(episodeId: string) {
  const all = useTasks();
  return [...all].reverse().find(task => task.episodeId === episodeId);
}
