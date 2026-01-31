from django.shortcuts import render

def war_stream(request):
    return render(request, "marketdata/War-Stream.html")
