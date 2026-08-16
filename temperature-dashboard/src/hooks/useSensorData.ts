import { useState, useEffect } from "react";
import { buildApiUrl } from "../api";

export const useSensorData = () => {
  const [sensorSessions, setSensorSessions] = useState<{ sensor_session: string; custom_text: string }[]>([]);
  const [data, setData] = useState<{ [sensor_session: string]: { timestamp: string; temperature: number }[] }>({});
  const [comments, setComments] = useState<{ [sensor_session: string]: { timestamp: string; temperature: number; comment: string }[] }>({});
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setIsLoading(true);
    fetch(buildApiUrl('/series'))
      .then((res) => {
        if (!res.ok) throw new Error(`Fehler beim Abrufen der Sensoren: ${res.statusText}`);
        return res.json();
      })
      .then((data) => {
        const sessions = data.map((item: { sensor_session: string; custom_text: string }) => ({
          sensor_session: item.sensor_session,
          custom_text: item.custom_text,
        }));
        setSensorSessions(sessions);
      })
      .catch((err) => {
        console.error(err);
        setError("Fehler beim Laden der Sensoren." + err);
      })
      .finally(() => setIsLoading(false));
  }, []);

  const fetchSensorData = (selected: string[]) => {
    if (selected.length === 0) {
      setComments({});
      setData({});
      return;
    }

    setIsLoading(true);
    setError(null);

    // Filtere _calibrated Sessions aus - diese werden nur über WebSocket aktualisiert
    const sessionsToFetch = selected.filter(session => !session.endsWith('_calibrated'));
    
    if (sessionsToFetch.length === 0) {
      // Nur _calibrated Sessions ausgewählt - keine API-Calls nötig
      setIsLoading(false);
      return;
    }

    Promise.all(
      sessionsToFetch.map((sensor_session) =>
  fetch(buildApiUrl(`/series/${sensor_session}`))
          .then((res) => {
            if (!res.ok) throw new Error(`Fehler beim Abrufen der Daten für ${sensor_session}: ${res.statusText}`);
            return res.json();
          })
          .then(({ data, comments }) => {
            console.log(`Daten für Sensor-Session ${sensor_session}:`, data);
            console.log(`Kommentare für Sensor-Session ${sensor_session}:`, comments);
            return { sensor_session, data, comments };
          })
      )
    )
      .then((results) => {
        const newData: { [sensor_session: string]: { timestamp: string; temperature: number }[] } = {};
        const newComments: { [sensor_session: string]: { timestamp: string; temperature: number; comment: string }[] } = {};
        
        results.forEach(({ sensor_session, data, comments }) => {
          newData[sensor_session] = data;
          newComments[sensor_session] = comments;
        });

        console.log("Alle erhaltenen Daten:", newData);
        console.log("Alle erhaltenen Kommentare:", newComments);

        // Behalte bestehende _calibrated Daten bei
        setData(prevData => {
          const calibratedData = Object.keys(prevData)
            .filter(key => key.endsWith('_calibrated'))
            .reduce((acc, key) => {
              acc[key] = prevData[key];
              return acc;
            }, {} as typeof prevData);
          
          return { ...newData, ...calibratedData };
        });
        
        setComments(newComments);
      })
      .catch((err) => {
        console.error(err);
        setError("Fehler beim Laden der Daten.");
      })
      .finally(() => setIsLoading(false));
  };

  const updateData = (sensor_session: string, timestamps: string[], temperatures: number[], replaceData = false) => {
    if (!sensor_session) {
      console.error("useSensorData: sensor_session is undefined. Cannot update data.");
      return;
    }

    setData((prevData) => {
      // Für Kalibrierungs-Previews: Immer erlauben, auch wenn Session noch nicht existiert
      if (sensor_session.endsWith('_calibrated')) {
        const updatedData = { ...prevData };
        const newMeasurements = timestamps.map((timestamp, index) => ({
          timestamp,
          temperature: temperatures[index],
        }));
        
        updatedData[sensor_session] = newMeasurements;
        console.log(`🔧 Kalibrierungs-Preview Daten für ${sensor_session} gesetzt: ${newMeasurements.length} Datenpunkte`);
        return updatedData;
      }
      
      const updatedData = { ...prevData };
      const newMeasurements = timestamps.map((timestamp, index) => ({
        timestamp,
        temperature: temperatures[index],
      }));

      // Für normale Sessions: Daten anhängen oder ersetzen
      if (replaceData) {
        updatedData[sensor_session] = newMeasurements;
        console.log(`Daten für ${sensor_session} ersetzt: ${newMeasurements.length} Datenpunkte`);
      } else {
        // Normale Behandlung: Daten anhängen
        updatedData[sensor_session] = [
          ...(updatedData[sensor_session] || []),
          ...newMeasurements,
        ];
      }

      return updatedData;
    });
  };

  const updateComments = (sensor_session: string, timestamp: string, temperature: number, comment: string) => {
    if (!sensor_session) {
      console.error("useSensorData: sensor_session is undefined. Cannot update comments.");
      return;
    }

    setComments((prevComments) => {
      const updatedComments = { ...prevComments };
      const newComment = { timestamp, temperature, comment };

      // Ensure the sensor_session key exists and append the new comment
      updatedComments[sensor_session] = [
        ...(updatedComments[sensor_session] || []),
        newComment,
      ];

      return updatedComments;
    });
  };
  
  const deleteComment = (sensor_session: string, timestamp: string, temperature: number, comment: string) => {
    if (!sensor_session) {
      console.error("useSensorData: sensor_session is undefined. Cannot delete comment.");
      return;
    }
    setComments((prevComments) => {
      const updatedComments = { ...prevComments };
      if (updatedComments[sensor_session]) {
        updatedComments[sensor_session] = updatedComments[sensor_session].filter(
          (c) => !(c.timestamp === timestamp && c.temperature === temperature && c.comment === comment)
        );
      }
      return updatedComments;
    });
  }

  const addNewSensorSession = (newSensorSession: { sensor_session: string; custom_text: string }) => {
    setSensorSessions((prevSessions) => {
      const updatedSessions = prevSessions.map((session) => {
        const existingSessionPart = session.sensor_session.split("_").pop();
        const newSessionPart = newSensorSession.sensor_session.split("_").pop();

        return existingSessionPart === newSessionPart
          ? newSensorSession // Überschreiben, wenn der hintere Teil übereinstimmt
          : session;
      });

      if (!prevSessions.some(session => session.sensor_session.split("_").pop() === newSensorSession.sensor_session.split("_").pop())) {
        return [...updatedSessions, newSensorSession];
      }

      return updatedSessions;
    });
  };

  return { sensorSessions, data, comments, isLoading, error, fetchSensorData, updateData, updateComments, deleteComment, addNewSensorSession };
};