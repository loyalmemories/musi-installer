package main

import (
	"bytes"
	"encoding/json"
	"github.com/majd/ipatool/v2/pkg/appstore"
	"testing"
	"time"
)

type progressStore struct{ appstore.AppStore }

func (s progressStore) Download(input appstore.DownloadInput) (appstore.DownloadOutput, error) {
	input.Progress.ChangeMax64(100)
	input.Progress.Set64(40)
	time.Sleep(350 * time.Millisecond)
	input.Progress.Set64(100)
	return appstore.DownloadOutput{DestinationPath: "test.ipa"}, nil
}
func TestDownloadEmitsMeasuredProgress(t *testing.T) {
	var output bytes.Buffer
	saved := encoder
	encoder = json.NewEncoder(&output)
	defer func() { encoder = saved }()
	_, err := downloadWithProgress(progressStore{}, appstore.DownloadInput{})
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(&output)
	measured := false
	for decoder.More() {
		var e event
		if err := decoder.Decode(&e); err != nil {
			t.Fatal(err)
		}
		if e.Type == "progress" {
			if e.Progress != 0.4 || e.Bytes != 40 || e.Total != 100 {
				t.Fatalf("unexpected progress: %+v", e)
			}
			measured = true
		}
	}
	if !measured {
		t.Fatal("no measured progress emitted")
	}
}

func TestMemoryKeysCopiesData(t *testing.T) {
	keys := memoryKeys{}
	data := []byte("secret")
	keys.Set("account", data)
	data[0] = 'x'
	saved, _ := keys.Get("account")
	if string(saved) != "secret" {
		t.Fatal("input aliased")
	}
	keys.Remove("account")
	if _, err := keys.Get("account"); err == nil {
		t.Fatal("credential remained")
	}
}
